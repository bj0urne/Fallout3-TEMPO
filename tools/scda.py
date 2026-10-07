"""Structural checker for compiled Fallout 3 script bytecode (SCDA).

Statement layout: opcode u16, length u16, data[length].
  0x10 Begin   data: blockType u16, jump u32 (bytes from end of Begin through end of End), params
  0x11 End
  0x16 If      data: jump u16 (statements strictly between it and the matching Else/ElseIf/EndIf;
               a ref-prefixed statement counts once), exprLen u16, expr
  0x18 ElseIf  same as If
  0x17 Else    data: jump u16
  0x19 EndIf
Expressions are postfix, space separated: 's'/'f' + u16 local index, 'X' + u16 opcode + u16 params,
ASCII numbers and operators.
"""
import struct

BEGIN, END, IF, ELSE, ELSEIF, ENDIF = 0x10, 0x11, 0x16, 0x17, 0x18, 0x19


REF_PREFIX = 0x1C  # "ref." prefix: opcode u16, ref index u16, then the real statement follows


def statements(code):
    off, out = 0, []
    while off < len(code):
        op, ln = struct.unpack_from("<HH", code, off)
        if op == REF_PREFIX:
            off += 4
            continue
        out.append((off, op, code[off + 4:off + 4 + ln]))
        off += 4 + ln
    if off != len(code):
        raise ValueError(f"statement stream overruns code ({off} != {len(code)})")
    return out


def check(code):
    """Return a list of problems (empty if the bytecode is structurally consistent)."""
    problems = []
    try:
        st = statements(code)
    except (ValueError, struct.error) as e:
        return [f"unparseable: {e}"]
    for i, (off, op, data) in enumerate(st):
        if op == BEGIN:
            jump = struct.unpack_from("<I", data, 2)[0]
            start = off + 4 + len(data)
            # find matching End
            j = next((k for k in range(i + 1, len(st)) if st[k][1] == END), None)
            if j is None:
                problems.append(f"Begin at {off:#x} has no End")
                continue
            end_after = st[j][0] + 4 + len(st[j][2])
            if start + jump != end_after:
                problems.append(f"Begin at {off:#x}: jump {jump} != {end_after - start}")
        elif op in (IF, ELSEIF, ELSE):
            jump = struct.unpack_from("<H", data, 0)[0]
            depth, k = 0, i + 1
            while k < len(st):
                kop = st[k][1]
                if kop == IF:
                    depth += 1
                elif kop == ENDIF:
                    if depth == 0:
                        break
                    depth -= 1
                elif kop in (ELSE, ELSEIF) and depth == 0:
                    break
                k += 1
            if k - i - 1 != jump:
                problems.append(f"{'If' if op == IF else 'ElseIf' if op == ELSEIF else 'Else'} at {off:#x}: "
                                f"jump {jump} != {k - i - 1}")
            if op in (IF, ELSEIF):
                elen = struct.unpack_from("<H", data, 2)[0]
                if 4 + elen != len(data):
                    problems.append(f"If at {off:#x}: expr length {elen} != {len(data) - 4}")
    return problems
