"""预先构造独立小样本，参考实现和隐藏测试不会进入模型请求。"""
import json
from pathlib import Path

TASKS = []


def add(id, name, prompt, reference, tests, old, new):
    assert old in reference and old != new
    TASKS.append(dict(id=id, entry_point=name, prompt=prompt, reference=reference,
                      tests=tests, mutants=[dict(name="单规则偏差", code=reference.replace(old, new))]))


add("V001", "split_record", """实现 split_record(s)。s 是一个字符串，表示用分号分隔的一条记录；换行也只是普通字符，不表示记录结束。返回字段字符串列表。
空输入返回 ['']；连续或末尾分号产生空字段。所有空白原样保留。
字段可以不加引号，也可以从字段的第一个字符开始用双引号包围。不加引号的字段不能含双引号。
加引号的字段内部允许分号；两个连续双引号代表一个字面双引号；结束双引号后必须紧接分号或输入结束，不能有空格。未闭合引号或其他违反这些规则的输入均抛 ValueError。
例如 split_record('a;"b;c";') == ['a', 'b;c', '']。""", '''def split_record(s):
    result, i = [], 0
    while True:
        out = []
        if i < len(s) and s[i] == '"':
            i += 1
            while True:
                if i == len(s): raise ValueError()
                if s[i] != '"':
                    out.append(s[i]); i += 1
                elif i + 1 < len(s) and s[i+1] == '"':
                    out.append('"'); i += 2
                else:
                    i += 1; break
            if i < len(s) and s[i] != ';': raise ValueError()
        else:
            while i < len(s) and s[i] != ';':
                if s[i] == '"': raise ValueError()
                out.append(s[i]); i += 1
        result.append(''.join(out))
        if i == len(s): return result
        i += 1
''', r'''assert split_record('') == ['']
assert split_record(';;') == ['', '', '']
assert split_record('a;"b;c";') == ['a', 'b;c', '']
assert split_record('"";"a""b"') == ['', 'a"b']
assert split_record(' a ;" x ";\n') == [' a ', ' x ', '\n']
assert split_record('"\n"') == ['\n']
for bad in ['"', '"a" ', 'a"b', '"a"b', ' "a"', '"""']:
    try: split_record(bad)
    except ValueError: pass
    else: raise AssertionError(bad)
import itertools
for fields in itertools.product(['', 'x', 'a;b', '"', '\n', ' x '], repeat=3):
    encoded = ';'.join('"' + f.replace('"', '""') + '"' for f in fields)
    assert split_record(encoded) == list(fields)
''', "result.append(''.join(out))", "result.append(''.join(out).strip())")

add("V002", "normalize_segments", """实现 normalize_segments(path)，输入是以 / 开头的字符串，否则抛 ValueError。返回规范化路径段的元组。
先按原始字符 / 分段，丢弃原始空段；再对每段做恰好一次百分号解码：每个 % 后必须是两个 ASCII 十六进制数字，否则抛 ValueError。非转义字符按 UTF-8 编码，与转义字节拼接后按严格 UTF-8 解码，非法 UTF-8 抛 ValueError。
解码后恰为 . 的段忽略；恰为 .. 的段弹出一个已保留段，若没有段可弹出则抛 ValueError。其他段按解码结果原样保留。解码所得 / 只是段内字符，不再次分段；+ 是普通加号，不替换为空格。
例如 normalize_segments('/a/%2F/%252e') == ('a', '/', '%2e')。""", '''def normalize_segments(path):
    if not path.startswith('/'): raise ValueError()
    result = []
    for raw in path.split('/'):
        if not raw: continue
        data = bytearray(); i = 0
        while i < len(raw):
            if raw[i] == '%':
                if i+2 >= len(raw) or any(c not in '0123456789abcdefABCDEF' for c in raw[i+1:i+3]): raise ValueError()
                data.append(int(raw[i+1:i+3], 16)); i += 3
            else:
                data.extend(raw[i].encode('utf-8')); i += 1
        try: segment = data.decode('utf-8')
        except UnicodeError: raise ValueError() from None
        if segment == '.': continue
        if segment == '..':
            if not result: raise ValueError()
            result.pop()
        else: result.append(segment)
    return tuple(result)
''', r'''for path, expected in [('/', ()), ('///', ()), ('/a/./b/..', ('a',)), ('/a/%2F/%252e', ('a','/','%2e')), ('/%E4%B8%AD/文', ('中','文')), ('/a+b/%00/\n', ('a+b','\x00','\n')), ('/a/%2e%2e/b', ('b',)), ('/%2e%2e%2f', ('../',))]:
    assert normalize_segments(path) == expected, path
for path in ['', 'a', '/../x', '/%2E%2e', '/%', '/%1', '/%GG', '/%FF', '/%C0%AF', '/%E4%B8', '/%１２']:
    try: normalize_segments(path)
    except ValueError: pass
    else: raise AssertionError(path)
from urllib.parse import quote
for text in ['abc', '中文', '+', '\n', '\x00', 'a/b', '%2e', '💡']:
    assert normalize_segments('/' + quote(text, safe='')) == (text,)
''', "else: result.append(segment)", "else: result.extend(segment.split('/'))")

add("V003", "allocate", """实现 allocate(total, weights)。total 为任意整数，weights 为非负整数列表。返回等长整数列表，和为 total。
分配时先取 abs(total)，按权重比例算精确份额，先分配各份额的向下取整值；余下单位按小数余数从大到小逐一分配，余数相同时下标小者优先。最后若 total 为负则将所有分配值取负。禁止用浮点数近似比例。
权重和为 0 时，仅当 total == 0 才返回等长全零列表，否则抛 ValueError。空列表也按此规则处理。
例如 allocate(-2, [1,1,1]) == [-1,-1,0]。""", '''def allocate(total, weights):
    W = sum(weights)
    if not W:
        if total: raise ValueError()
        return [0] * len(weights)
    parts = [divmod(abs(total) * w, W) for w in weights]
    out = [q for q, r in parts]
    for i in sorted(range(len(weights)), key=lambda i: (-parts[i][1], i))[:abs(total)-sum(out)]: out[i] += 1
    return [-v for v in out] if total < 0 else out
''', '''assert allocate(-2,[1,1,1]) == [-1,-1,0]
assert allocate(2,[1,1,1]) == [1,1,0]
assert allocate(5,[0,1,1]) == [0,3,2]
assert allocate(0,[]) == [] and allocate(0,[0,0]) == [0,0]
assert allocate(10**50+1,[1,1]) == [5*10**49+1,5*10**49]
for weights in [[], [0], [0,0]]:
    for total in [-1,1]:
        try: allocate(total,weights)
        except ValueError: pass
        else: raise AssertionError()
from fractions import Fraction
import itertools
for weights in itertools.product(range(3),repeat=3):
    if not sum(weights): continue
    for total in range(-9,10):
        quotas = [Fraction(abs(total)*w,sum(weights)) for w in weights]
        floors = [q.numerator//q.denominator for q in quotas]
        ranking = sorted(range(3),key=lambda i: (quotas[i]-floors[i], -i), reverse=True)
        expected = floors[:]
        for i in ranking[:abs(total)-sum(floors)]: expected[i] += 1
        if total < 0: expected = [-x for x in expected]
        assert allocate(total,list(weights)) == expected
''', "(-parts[i][1], i)", "(-parts[i][1], -i)")

add("V004", "apply_transfers", """实现 apply_transfers(balances, events)，返回 (新余额字典, 状态列表)，不得修改输入。balances 是账户名到非负整数的字典。events 是 (id, source, target, amount) 元组列表；id/source/target 是字符串，amount 是整数。
按给定顺序处理事件。同一 id 仅第一次出现参与处理：不论第一次被接纳还是拒绝，后续同 id 一律状态 duplicate，不再检查其内容。
第一次出现的事件，仅当源和目标账户均存在、amount >= 0 且源账户当前余额 >= amount 时接纳，状态 applied；否则状态 rejected，不改变任何余额。
接纳时从源账户扣除 amount 再给目标账户增加 amount。源和目标相同也须检查以上条件，通过后净余额不变。金额 0 也不能绕过账户存在检查。""", '''def apply_transfers(balances, events):
    out, statuses, seen = dict(balances), [], set()
    for id, source, target, amount in events:
        if id in seen:
            statuses.append('duplicate'); continue
        seen.add(id)
        if source not in out or target not in out or amount < 0 or out[source] < amount:
            statuses.append('rejected'); continue
        out[source] -= amount
        out[target] += amount
        statuses.append('applied')
    return out, statuses
''', '''import copy
b = {'a': 4, 'b': 0}
events = [('x','a','b',5), ('x','a','b',1), ('y','a','a',4), ('z','a','a',5), ('p','a','b',4), ('q','a','b',0), ('r','missing','b',0), ('s','b','a',-1), ('p','?', '?',-2)]
saved = copy.deepcopy((b,events))
assert apply_transfers(b,events) == ({'a':0,'b':4}, ['rejected','duplicate','applied','rejected','applied','applied','rejected','rejected','duplicate'])
assert (b,events) == saved
assert apply_transfers({},[]) == ({},[])
assert apply_transfers({},[('x','a','a',0)]) == ({},['rejected'])
for n in range(10):
    out, status = apply_transfers({'a':n,'b':0}, [('x','a','b',n),('y','b','a',n),('x','a','b',n)])
    assert out == {'a':n,'b':0} and status == ['applied','applied','duplicate']
''', "seen.add(id)", "if source in out and target in out and amount >= 0 and out[source] >= amount: seen.add(id)")

add("V005", "path_match", """实现 path_match(pattern, text)，两个参数均为任意 Unicode 字符串，返回布尔值，要求整串匹配，换行是普通字符。
从左到右解析 pattern：反斜杠转义紧随其后的一个字符；末尾孤立反斜杠代表字面反斜杠。未转义的两个连续星号优先组成 **，匹配零个或多个任意字符，包括 / 和换行。单个 * 匹配零个或多个非 / 字符。? 匹配恰好一个非 / 字符。其他字符按字面匹配。
连续三个星号按 ** 然后 * 解析；** 不具有‘可省略相邻斜杠’等额外规则。例如 a/**/b 不匹配 a/b，但匹配 a//b。""", r'''from functools import lru_cache
def path_match(pattern,text):
    tokens = []; i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == '\\':
            tokens.append(('lit',pattern[i+1] if i+1 < len(pattern) else '\\')); i += 2
        elif pattern.startswith('**',i): tokens.append(('all','')); i += 2
        elif c in '*?': tokens.append((c,'')); i += 1
        else: tokens.append(('lit',c)); i += 1
    @lru_cache(None)
    def match(i,j):
        if i == len(tokens): return j == len(text)
        kind,value = tokens[i]
        if kind == 'lit': return j < len(text) and text[j] == value and match(i+1,j+1)
        if kind == '?': return j < len(text) and text[j] != '/' and match(i+1,j+1)
        return match(i+1,j) or (j < len(text) and (kind == 'all' or text[j] != '/') and match(i,j+1))
    return match(0,0)
''', r'''assert path_match('a/**/b','a//b') and not path_match('a/**/b','a/b')
assert path_match('*','\n') and path_match('?','\n') and path_match('**','a/\nb')
assert not path_match('a','a\n') and not path_match('*','a/b')
assert path_match(r'\*','*') and not path_match(r'\*','x')
assert path_match('\\','\\') and path_match(r'\?','?')
assert path_match('***','a/b') and path_match('','') and not path_match('','x')
assert path_match('[a]','[a]') and not path_match('[a]','a')
import re,itertools
for atoms in itertools.product(['a','/','*','**','?',r'\*','\n'],repeat=2):
    pattern = ''.join(atoms)
    regex = ''; i = 0
    while i < len(pattern):
        if pattern[i] == '\\':
            regex += re.escape(pattern[i+1] if i+1 < len(pattern) else '\\'); i += 2
        elif pattern.startswith('**',i): regex += r'[\s\S]*'; i += 2
        elif pattern[i] == '*': regex += '[^/]*'; i += 1
        elif pattern[i] == '?': regex += '[^/]'; i += 1
        else: regex += re.escape(pattern[i]); i += 1
    for chars in itertools.product(['a','/','*','\n'],repeat=2):
        text = ''.join(chars)
        assert path_match(pattern,text) == bool(re.fullmatch(regex,text)), (pattern,text)
''', "(kind == 'all' or text[j] != '/')", "(text[j] != '/')")

add("V006", "compare_versions", """实现 compare_versions(a,b)，输入为两个字符串。先完整校验两者，任何一个不合法都抛 ValueError；合法时按以下版本规则比较，返回 -1、0 或 1。
格式是 major.minor.patch，可选 -预发布段，可选 +构建段（只能按此顺序）。major/minor/patch 都是非空 ASCII 数字，不允许前导零，数字 0 本身允许；不限制整数位数在 100 位以内的合法输入。
预发布段和构建段都由一个或多个非空标识符用点分隔；标识符只允许 ASCII 字母、数字和连字符。预发布中纯数字标识符不允许前导零；构建段纯数字可有前导零。不允许任何空白或尾随换行。
依次比较三个版本整数；相同则正式版大于有预发布段的版本。双方都有预发布时逐个比较标识符：两个纯数字按整数比，纯数字小于非纯数字，两个非纯数字按 ASCII 字典序比；公共前缀相同则标识符较少的一方小。构建段仅校验、不参与比较。""", r'''import re
def compare_versions(a,b):
    def parse(s):
        match = re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?',s)
        if not match: raise ValueError()
        major,minor,patch,pre,build = match.groups()
        parts = pre.split('.') if pre is not None else None
        if parts is not None and any(p.isdigit() and len(p)>1 and p[0]=='0' for p in parts): raise ValueError()
        return (int(major),int(minor),int(patch)),parts
    core_a,pre_a = parse(a); core_b,pre_b = parse(b)
    if core_a != core_b: return (core_a>core_b)-(core_a<core_b)
    if pre_a is None or pre_b is None: return (pre_a is None)-(pre_b is None)
    for x,y in zip(pre_a,pre_b):
        kx = (0,int(x)) if x.isdigit() else (1,x)
        ky = (0,int(y)) if y.isdigit() else (1,y)
        if kx != ky: return (kx>ky)-(kx<ky)
    return (len(pre_a)>len(pre_b))-(len(pre_a)<len(pre_b))
''', r'''ordered = ['1.0.0-alpha','1.0.0-alpha.1','1.0.0-alpha.beta','1.0.0-beta','1.0.0-beta.2','1.0.0-beta.11','1.0.0-rc.1','1.0.0','1.0.1','2.0.0']
for i,a in enumerate(ordered):
    for j,b in enumerate(ordered): assert compare_versions(a,b) == (i>j)-(i<j)
assert compare_versions('1.0.0+001.a','1.0.0+zzz') == 0
assert compare_versions('1.0.0-9','1.0.0-10') == -1
assert compare_versions('1.0.0-0','1.0.0-A') == -1
assert compare_versions('1.0.0-A','1.0.0-a') == -1
assert compare_versions('9'*100+'.0.0','2.0.0') == 1
for bad in ['1.0.0\n','１.0.0','1.0','01.0.0','1.0.0-01','1.0.0-','1.0.0+','1.0.0-a..b','1.0.0+a..b','1.0.0-é','1.0.0 x','1.0.0+a+b']:
    for a,b in [(bad,'0.0.0'),('0.0.0',bad)]:
        try: compare_versions(a,b)
        except ValueError: pass
        else: raise AssertionError((a,b))
''', "return (len(pre_a)>len(pre_b))-(len(pre_a)<len(pre_b))", "return 0")

add("V007", "weighted_quantile", """实现 weighted_quantile(items, numerator, denominator)。items 是 (整数值, 非负整数权重) 元组列表；numerator、denominator 为整数。必须先检查 denominator > 0 且 0 <= numerator <= denominator，否则抛 ValueError，即使 items 为空也要如此。
忽略零权重项，将相同值的权重合并。若没有正权重项返回 None。
否则，分位数是 numerator/denominator。分位数为 0 时返回所有正权重项中的最小值；其他情况下返回按值从小到大累计权重首次大于等于总权重乘以该分位数时的值。不得使用浮点近似；不得修改输入。""", '''def weighted_quantile(items,numerator,denominator):
    if denominator <= 0 or not 0 <= numerator <= denominator: raise ValueError()
    weights = {}
    for value,weight in items:
        if weight: weights[value] = weights.get(value,0)+weight
    if not weights: return None
    total = sum(weights.values()); current = 0
    for value in sorted(weights):
        current += weights[value]
        if current*denominator >= total*numerator: return value
''', '''assert weighted_quantile([],0,1) is None
assert weighted_quantile([(0,0)],1,1) is None
assert weighted_quantile([(-100,0),(2,1),(4,1)],0,2) == 2
assert weighted_quantile([(4,1),(2,1)],1,2) == 2
assert weighted_quantile([(4,1),(2,1)],1,1) == 4
assert weighted_quantile([(1,10**50),(2,1)],10**50+1,10**50+1) == 2
for a,b in [(0,0),(1,-1),(-1,3),(4,3)]:
    try: weighted_quantile([],a,b)
    except ValueError: pass
    else: raise AssertionError()
import itertools,copy
for ws in itertools.product(range(3), repeat=3):
    items = [(3,ws[0]),(-1,ws[1]),(3,ws[2])]
    saved = copy.deepcopy(items)
    expanded = sorted(v for v,w in items for _ in range(w))
    for q in range(6):
        expected = expanded[max(0,(len(expanded)*q+4)//5-1)] if expanded else None
        assert weighted_quantile(items,q,5) == expected
        assert items == saved
''', "current*denominator >= total*numerator", "current*denominator > total*numerator or numerator == denominator")

add("V008", "union_intervals", """实现 union_intervals(intervals)，返回实数轴上区间并集的规范表示。输入是 (lo, hi, left_closed, right_closed) 元组列表，lo/hi 是 -10 到 10 的整数且 lo <= hi，两个 closed 字段为布尔值。不得修改输入。
端点是否包含由 closed 标记决定；lo == hi 时仅两端均闭才表示单点，否则是空区间，应忽略。
输出按左端点升序排列的元组列表，覆盖与输入恰好相同的实数点，并将能连成一个区间的部分最大限度合并。两个区间端点相接时，至少其中一方包含接点才可合并；双方均不包含接点则不能合并。单点区间也可以填补该接点。
例如 [(0,1,False,False),(1,2,True,False)] 合并为 [(0,2,False,False)]。""", '''def union_intervals(intervals):
    # 用有限端点和相邻开区间的覆盖情况构造，避免依赖常见合并循环。
    points = sorted({x for lo,hi,lc,rc in intervals for x in (lo,hi)})
    if not points: return []
    def contains(x):
        return any((lo < x < hi) or (lo == x and lc and (lo < hi or rc)) or (hi == x and rc and (lo < hi or lc)) for lo,hi,lc,rc in intervals)
    atoms = []
    for i,p in enumerate(points):
        atoms.append((p,p,True,True,contains(p)))
        if i+1 < len(points):
            q = points[i+1]
            atoms.append((p,q,False,False,contains((p+q)/2)))
    result = []; group = []
    for atom in atoms + [(0,0,False,False,False)]:
        if atom[4]: group.append(atom)
        elif group:
            result.append((group[0][0],group[-1][1],group[0][2],group[-1][3])); group=[]
    return result
''', '''assert union_intervals([]) == []
assert union_intervals([(0,1,False,False),(1,2,True,False)]) == [(0,2,False,False)]
assert union_intervals([(0,1,True,False),(1,2,False,True)]) == [(0,1,True,False),(1,2,False,True)]
assert union_intervals([(0,1,False,False),(1,2,False,False),(1,1,True,True)]) == [(0,2,False,False)]
assert union_intervals([(1,1,False,True),(2,2,True,False),(0,0,False,False)]) == []
assert union_intervals([(0,2,False,False),(0,1,True,True),(2,2,True,True)]) == [(0,2,True,True)]
assert union_intervals([(2,3,True,True),(0,4,False,False),(0,4,True,False)]) == [(0,4,True,False)]
import random,copy
rng = random.Random(20261031)
def inside(row,x):
    lo,hi,lc,rc = row
    return (lo < x or (lc and lo == x)) and (x < hi or (rc and hi == x))
for _ in range(100):
    rows = []
    for j in range(rng.randrange(8)):
        lo,hi = sorted([rng.randrange(-3,4),rng.randrange(-3,4)])
        rows.append((lo,hi,bool(rng.randrange(2)),bool(rng.randrange(2))))
    saved = copy.deepcopy(rows); out = union_intervals(rows)
    assert rows == saved
    assert all(lo < hi or (lo == hi and lc and rc) for lo,hi,lc,rc in out)
    for a,b in zip(out,out[1:]):
        assert a[1] < b[0] or (a[1] == b[0] and not a[3] and not b[2])
    for twice in range(-7,8):
        x = twice/2
        assert any(inside(a,x) for a in rows) == any(inside(b,x) for b in out), (rows,out,x)
''', "if atom[4]: group.append(atom)", "if atom[4] and atom[0] != atom[1]: group.append(atom)")


if __name__ == "__main__":
    path = Path(__file__).with_name("tasks.json")
    path.write_text(json.dumps(TASKS, ensure_ascii=False, indent=2) + "\n")
    print(f"已写入 {len(TASKS)} 个任务")
