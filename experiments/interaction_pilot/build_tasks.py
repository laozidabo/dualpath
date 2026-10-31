"""构造第三批独立小样本：规则交互型函数题。

参考实现、隐藏测试与变异体都不会进入模型请求；生成后仅用于离线评分。
本题集与 V001-V008 不同，冻结后不得修改。
"""
import json
from pathlib import Path

TASKS = []


def add(task_id, name, prompt, reference, tests, old, new):
    assert old in reference, (task_id, old)
    assert old != new
    TASKS.append(
        dict(
            id=task_id,
            entry_point=name,
            prompt=prompt,
            reference=reference,
            tests=tests,
            mutants=[dict(name="单规则偏差", code=reference.replace(old, new))],
        )
    )


add(
    "I001",
    "allocate",
    """实现 allocate(amount, weights)。amount 是非负整数；weights 是非空列表或元组，元素全部是非负整数。
先做输入校验：任一输入类型不符合要求（注意 bool 不是合法整数）或 weights 之和为 0 时抛 ValueError（即使 amount 为 0 也一样）。校验通过后，amount 为 0 时返回与 weights 等长的全 0 列表。
否则按最大余数法分配：先给第 i 项 amount*weights[i]//total；再把剩下的 amount - sum(base) 个整数按余数 amount*weights[i]%total 从大到小各加 1，余数相同则下标小的优先。
返回列表，长度与 weights 相同，元素之和必须等于 amount。不得修改输入。
例如 allocate(10, [1, 1, 1]) == [4, 3, 3]。""",
    '''def allocate(amount, weights):
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
        raise ValueError
    if not isinstance(weights, (list, tuple)) or not weights:
        raise ValueError
    total = 0
    for weight in weights:
        if not isinstance(weight, int) or isinstance(weight, bool) or weight < 0:
            raise ValueError
        total += weight
    if total == 0:
        raise ValueError
    if amount == 0:
        return [0] * len(weights)
    base = [amount * weight // total for weight in weights]
    remaining = amount - sum(base)
    order = sorted(range(len(weights)), key=lambda i: (-(amount * weights[i] % total), i))
    for i in order[:remaining]:
        base[i] += 1
    return base
''',
    r'''assert allocate(0, [1, 1]) == [0, 0]
assert allocate(10, [1, 1, 1]) == [4, 3, 3]
assert allocate(7, [2, 3, 5]) == [1, 2, 4]
assert allocate(4, [1, 1, 1]) == [2, 1, 1]
assert allocate(5, [0, 1, 0]) == [0, 5, 0]
assert allocate(3, [1, 2]) == [1, 2]
assert allocate(1, [1, 1, 1, 1, 1, 1]) == [1, 0, 0, 0, 0, 0]
assert allocate(2, [0, 1, 1]) == [0, 1, 1]
assert allocate(2, [3, 5]) == [1, 1]
assert allocate(5, (1, 1)) == [3, 2]
for bad in [(1, []), (1, [0, 0]), (0, [0, 0]), (1, [1, -1]), (-1, [1]), (1.0, [1]), (1, [1.0]), (1, [True])]:
    try:
        allocate(*bad)
    except ValueError:
        pass
    else:
        raise AssertionError(bad)
import random
rng = random.Random(3)
for _ in range(200):
    n = rng.randrange(1, 6)
    ws = [rng.randrange(0, 5) for _ in range(n)]
    if not any(ws):
        continue
    amt = rng.randrange(0, 40)
    out = allocate(amt, ws)
    assert len(out) == n and sum(out) == amt and all(type(x) is int and x >= 0 for x in out)
weights = [1, 2, 3]
saved = list(weights)
allocate(7, weights)
assert weights == saved
''',
    "key=lambda i: (-(amount * weights[i] % total), i)",
    "key=lambda i: (-(amount * weights[i] % total), -i)",
)

add(
    "I002",
    "compare_versions",
    """实现 compare_versions(a, b)，比较两个语义化版本字符串，返回 -1、0 或 1。
版本格式为 MAJOR.MINOR.PATCH，并可选地追加 -PRERELEASE 与 +BUILD；BUILD 与 PRERELEASE 使用相同的标识符字符集。MAJOR/MINOR/PATCH 是非负十进制整数，不允许前导 0（"0" 本身除外）。BUILD 被忽略，但若出现 + 则后面必须是非空的点分标识符。PRERELEASE 由点分标识符组成，标识符只含 ASCII 字母、数字和连字符且非空；纯数字标识符按数值比较，其他标识符（纯字母或字母数字混合）按 ASCII 字典序比较，纯数字标识符小于非纯数字标识符；逐项比较，前缀相同时标识符更少者更小；有 PRERELEASE 的版本小于没有 PRERELEASE 的版本。
任何不合法的输入抛 ValueError。例如 compare_versions("1.0.0-alpha", "1.0.0") == -1。""",
    '''def compare_versions(a, b):
    def parse(text):
        if not isinstance(text, str) or not text:
            raise ValueError
        core, plus, build = text.partition("+")
        if plus:
            if not build:
                raise ValueError
            for item in build.split("."):
                if not item or not all(c.isascii() and (c.isalnum() or c == "-") for c in item):
                    raise ValueError
        main, dash, pre = core.partition("-")
        numbers = main.split(".")
        if len(numbers) != 3:
            raise ValueError
        parsed = []
        for number in numbers:
            if not number.isascii() or not number.isdigit() or (len(number) > 1 and number[0] == "0"):
                raise ValueError
            parsed.append(int(number))
        identifiers = []
        if dash:
            if not pre:
                raise ValueError
            for item in pre.split("."):
                if not item or not all(c.isascii() and (c.isalnum() or c == "-") for c in item):
                    raise ValueError
                identifiers.append(item)
        return parsed, identifiers

    def compare_pre(x, y):
        if not x and not y:
            return 0
        if not x:
            return 1
        if not y:
            return -1
        for left, right in zip(x, y):
            left_num, right_num = left.isdigit(), right.isdigit()
            if left_num and right_num:
                if int(left) != int(right):
                    return -1 if int(left) < int(right) else 1
            elif left_num != right_num:
                return -1 if left_num else 1
            elif left != right:
                return -1 if left < right else 1
        return (len(x) > len(y)) - (len(x) < len(y))

    left_main, left_pre = parse(a)
    right_main, right_pre = parse(b)
    if left_main != right_main:
        return -1 if left_main < right_main else 1
    return compare_pre(left_pre, right_pre)
''',
    r'''assert compare_versions("1.0.0", "1.0.0") == 0
assert compare_versions("1.0.0", "2.0.0") == -1
assert compare_versions("1.2.3", "1.2.2") == 1
assert compare_versions("1.0.0-alpha", "1.0.0") == -1
assert compare_versions("1.0.0-alpha.1", "1.0.0-alpha.beta") == -1
assert compare_versions("1.0.0-alpha.1", "1.0.0-alpha.2") == -1
assert compare_versions("1.0.0-alpha.1", "1.0.0-alpha") == 1
assert compare_versions("1.0.0-beta.2", "1.0.0-beta.11") == -1
assert compare_versions("1.0.0-rc.1", "1.0.0-rc.1+build.5") == 0
assert compare_versions("1.0.0-rc.1", "1.0.0-rc.1+build.6") == 0
assert compare_versions("1.0.0+build.1", "1.0.0+build.2") == 0
assert compare_versions("1.0.0-1a", "1.0.0-1b") == -1
assert compare_versions("1.0.0-1", "1.0.0-a") == -1
assert compare_versions("1.0.0-5", "1.0.0-4a") == -1
for bad in ["1.0", "1.0.0.0", "01.0.0", "1.0.0-", "1.0.0+", "1.0.0-alpha..1", "1.0.0-alpha_1", "", "1.0.x"]:
    for pair in ((bad, "1.0.0"), ("1.0.0", bad)):
        try:
            compare_versions(*pair)
        except ValueError:
            pass
        else:
            raise AssertionError(pair)
for bad in [None, 1, b"1.0.0"]:
    try:
        compare_versions(bad, "1.0.0")
    except ValueError:
        pass
    else:
        raise AssertionError(bad)
''',
    "        if dash:\n            if not pre:\n                raise ValueError\n            for item in pre.split(\".\"):\n                if not item or not all(c.isascii() and (c.isalnum() or c == \"-\") for c in item):\n                    raise ValueError\n                identifiers.append(item)",
    "        if dash:\n            if not pre:\n                raise ValueError\n            identifiers = []",
)

add(
    "I003",
    "wildcard_match",
    """实现 wildcard_match(pattern, text)，按以下规则做通配匹配，返回 bool。
pattern 与 text 必须是 str，否则抛 ValueError。从左到右解析：遇到连续 * 时先取两个构成 **，剩余的 * 再单独解析。规则从左到右完全匹配：
- 普通字符只匹配它自己；
- ? 匹配恰好一个非 / 字符；
- * 匹配零个或多个非 / 字符；
- ** 匹配零个或多个任意字符（包括 /）；
- \\ 转义下一个字符，使其按普通字符匹配；pattern 以孤立 \\ 结尾或转义不存在时抛 ValueError。
例如 wildcard_match("a*", "abc") 为 True，wildcard_match("a*", "a/b") 为 False。""",
    r'''def wildcard_match(pattern, text):
    if not isinstance(pattern, str) or not isinstance(text, str):
        raise ValueError
    index = 0
    while index < len(pattern):
        if pattern[index] == "\\":
            if index + 1 >= len(pattern):
                raise ValueError
            index += 2
        else:
            index += 1

    def match(p, t):
        if not p:
            return not t
        head = p[0]
        if head == "\\":
            if len(p) < 2:
                raise ValueError
            return bool(t) and t[0] == p[1] and match(p[2:], t[1:])
        if head == "*":
            if p.startswith("**"):
                return match(p[2:], t) or (bool(t) and match(p, t[1:]))
            return match(p[1:], t) or (bool(t) and t[0] != "/" and match(p, t[1:]))
        if head == "?":
            return bool(t) and t[0] != "/" and match(p[1:], t[1:])
        return bool(t) and head == t[0] and match(p[1:], t[1:])

    return match(pattern, text)
''',
    r'''assert wildcard_match("", "") is True
assert wildcard_match("", "a") is False
assert wildcard_match("a*", "abc") is True
assert wildcard_match("a*", "a/b") is False
assert wildcard_match("a**", "a/b") is True
assert wildcard_match("**", "a/b/c") is True
assert wildcard_match("a?c", "abc") is True
assert wildcard_match("a?c", "a/c") is False
assert wildcard_match("*", "") is True
assert wildcard_match("a*", "a") is True
assert wildcard_match("?", "") is False
assert wildcard_match("a\\*b", "a*b") is True
assert wildcard_match("a\\*b", "aXb") is False
assert wildcard_match("*/*", "a/b") is True
assert wildcard_match("*/*", "a/b/c") is False
assert wildcard_match("**/c", "a/b/c") is True
assert wildcard_match("***", "a/b") is True
for bad in ["\\", "a\\"]:
    for text in ("a", "", "b"):
        try:
            wildcard_match(bad, text)
        except ValueError:
            pass
        else:
            raise AssertionError((bad, text))
for bad in [(1, "a"), ("a", None), (b"a", "a")]:
    try:
        wildcard_match(*bad)
    except ValueError:
        pass
    else:
        raise AssertionError(bad)
''',
    "return match(p[1:], t) or (bool(t) and t[0] != \"/\" and match(p, t[1:]))",
    "return match(p[1:], t) or (bool(t) and match(p, t[1:]))",
)

add(
    "I004",
    "parse_duration",
    """实现 parse_duration(text)，把时长字符串解析成毫秒整数。text 必须是非空 str，否则抛 ValueError。
语法是若干「非负整数 + 单位」依次拼接，中间不允许空格或其他字符；单位只能是 h、m、s、ms。整数允许前导 0。单位按最长匹配：ms 优先于 m，s 与 h 无歧义。单位可以重复、顺序任意。
整个字符串必须被完整消费；出现空串、未知单位、负数、小数、空格或其他非法内容均抛 ValueError。例如 parse_duration("1h30m") == 5400000，parse_duration("10ms") == 10。""",
    '''def parse_duration(text):
    if not isinstance(text, str) or not text:
        raise ValueError
    units = {"h": 3600000, "m": 60000, "s": 1000, "ms": 1}
    index = 0
    total = 0
    seen = False
    while index < len(text):
        start = index
        while index < len(text) and text[index].isdigit():
            index += 1
        if index == start:
            raise ValueError
        number = int(text[start:index])
        if text.startswith("ms", index):
            unit = "ms"
        elif index < len(text) and text[index] in "hms":
            unit = text[index]
        else:
            raise ValueError
        index += len(unit)
        total += number * units[unit]
        seen = True
    if not seen:
        raise ValueError
    return total
''',
    r'''assert parse_duration("1h") == 3600000
assert parse_duration("1h30m") == 5400000
assert parse_duration("2m3s") == 123000
assert parse_duration("10ms") == 10
assert parse_duration("1ms") == 1
assert parse_duration("1h1m1s1ms") == 3661001
assert parse_duration("0s") == 0
assert parse_duration("0001s") == 1000
assert parse_duration("1s1s") == 2000
assert parse_duration("1m2h") == 7260000
for bad in ["", "10", "1x", "1m 2s", "-1s", "1.5s", "h", "1h30", " 1s", "1s ", "1S", 1, None, b"1s"]:
    try:
        parse_duration(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(bad)
''',
    "        if text.startswith(\"ms\", index):\n            unit = \"ms\"\n        elif index < len(text) and text[index] in \"hms\":\n            unit = text[index]",
    "        if index < len(text) and text[index] in \"hms\":\n            unit = text[index]\n        elif text.startswith(\"ms\", index):\n            unit = \"ms\"",
)

add(
    "I005",
    "dedupe_events",
    """实现 dedupe_events(events)，输入是由二元组 (key, payload) 组成的列表或元组，key 与 payload 都必须是 str，否则抛 ValueError。events 必须是 list 或 tuple，每个元素必须是长度 2 的 tuple；违反任一约束抛 ValueError。
按 key 去重，但保留第一次出现的位置：返回值是按 key 首次出现顺序排列的 (key, payload) 元组列表，payload 取该 key 最后一次出现的值。不得修改输入。
例如 dedupe_events([("a", "1"), ("b", "2"), ("a", "3")]) == [("a", "3"), ("b", "2")]。""",
    '''def dedupe_events(events):
    if not isinstance(events, (list, tuple)):
        raise ValueError
    order = []
    latest = {}
    for item in events:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError
        key, payload = item
        if not isinstance(key, str) or not isinstance(payload, str):
            raise ValueError
        if key not in latest:
            order.append(key)
        latest[key] = payload
    return [(key, latest[key]) for key in order]
''',
    r'''assert dedupe_events([]) == []
assert dedupe_events([("a", "1")]) == [("a", "1")]
assert dedupe_events([("a", "1"), ("b", "2"), ("a", "3")]) == [("a", "3"), ("b", "2")]
assert dedupe_events([("b", "1"), ("a", "2"), ("b", "3"), ("a", "4")]) == [("b", "3"), ("a", "4")]
assert dedupe_events([("x", ""), ("x", "y"), ("x", "z")]) == [("x", "z")]
assert dedupe_events((("k", "1"), ("k", "2"))) == [("k", "2")]
items = [("a", "1"), ("b", "2")]
saved = list(items)
dedupe_events(items)
assert items == saved
for bad in [[("a",)], [("a", "b", "c")], [(1, "b")], [("a", 2)], ["ab"], [["a", "b"]]]:
    try:
        dedupe_events(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(bad)
''',
    "        if key not in latest:\n            order.append(key)\n        latest[key] = payload",
    "        if key in latest:\n            order.remove(key)\n        order.append(key)\n        latest[key] = payload",
)

add(
    "I006",
    "token_bucket",
    """实现 token_bucket(capacity, refill, events)，模拟整数令牌桶并返回每次事件是否被接受。
capacity 与 refill 是非负整数（bool 不合法），capacity 是桶容量（单位：token），refill 是每 1000 毫秒补充的 token 数。events 是 (timestamp_ms, cost) 二元组的列表，timestamp_ms 是非递减非负整数，cost 是非负整数 token 数。
桶初始为满。每个事件先按距上一事件的时间差补充 token（按毫秒线性补充并累积余量，补充后不超过容量），再在余额足够 cost 时接受并扣除，否则拒绝。第一个事件的时间戳同样从 0 时刻开始计算。返回 bool 列表。
任一输入不满足上述类型或取值约束（bool 不算整数；events 必须是 list 或 tuple，每个元素是长度 2 的 tuple；时间戳必须非递减）时抛 ValueError。
例如 capacity=1、refill=1、events=[(0,1),(500,1),(1000,1)] 返回 [True, False, True]。""",
    '''def token_bucket(capacity, refill, events):
    if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 0:
        raise ValueError
    if not isinstance(refill, int) or isinstance(refill, bool) or refill < 0:
        raise ValueError
    if not isinstance(events, (list, tuple)):
        raise ValueError
    credit = capacity * 1000
    last = 0
    result = []
    for event in events:
        if not isinstance(event, tuple) or len(event) != 2:
            raise ValueError
        timestamp, cost = event
        if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < last:
            raise ValueError
        if not isinstance(cost, int) or isinstance(cost, bool) or cost < 0:
            raise ValueError
        credit = min(capacity * 1000, credit + (timestamp - last) * refill)
        last = timestamp
        if credit >= cost * 1000:
            credit -= cost * 1000
            result.append(True)
        else:
            result.append(False)
    return result
''',
    r'''assert token_bucket(1, 1, [(0, 1), (500, 1), (1000, 1)]) == [True, False, True]
assert token_bucket(2, 1, [(0, 2), (1000, 1)]) == [True, True]
assert token_bucket(1, 0, [(0, 1), (999, 1), (1000, 1)]) == [True, False, False]
assert token_bucket(0, 5, [(0, 0), (100, 1)]) == [True, False]
assert token_bucket(2, 1, []) == []
assert token_bucket(1, 1, [(1000, 1)]) == [True]
assert token_bucket(1, 2, [(0, 1), (500, 1), (500, 1)]) == [True, True, False]
assert token_bucket(1, 10, [(0, 0), (100, 1), (100, 1)]) == [True, True, False]
for bad in [(-1, 1, []), (1, -1, []), (1, 1, [(1, 1), (0, 1)]), (1, 1, [(-1, 1)]), (1.0, 1, []), (1, 1, [(0, -1)]), (True, 1, []), (1, 1, "x"), (1, 1, [(0, 1, 2)])]:
    try:
        token_bucket(*bad)
    except ValueError:
        pass
    else:
        raise AssertionError(bad)
''',
    "        credit = min(capacity * 1000, credit + (timestamp - last) * refill)",
    "        credit = min(capacity * 1000, (timestamp - last) * refill)",
)

add(
    "I007",
    "parse_config",
    """实现 parse_config(text)，把配置文本解析成 dict[str, str]。text 必须是 str，否则抛 ValueError。
按 \\n 分行，逐行去掉首尾空白；空行与以 # 开头的行忽略。每行必须包含 =，否则抛 ValueError；key 取第一个 = 之前的部分并去掉首尾空白，必须非空且不含空白字符；value 取第一个 = 之后的部分并去掉首尾空白。
引号必须成对：value 长度至少为 2 才可去掉一对首尾相同的引号，引号内的内容原样保留（其中的 #、=、空格都不特殊）。若 value 以引号开头但不以同种引号结尾，或 value 恰好只有一个引号字符，抛 ValueError。未加引号的 value 中，第一个 # 之后视为注释并去掉，再对剩余部分去掉首尾空白。
同一个 key 出现两次抛 ValueError。返回按首次出现顺序的 dict。例如 parse_config('a=1 # note\\nb="x # y"') == {'a': '1', 'b': 'x # y'}。""",
    '''def parse_config(text):
    if not isinstance(text, str):
        raise ValueError
    result = {}
    for raw in text.split("\\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or any(c.isspace() for c in key):
            raise ValueError
        value = value.strip()
        if len(value) >= 2 and value[0] in "\\"'":
            quote = value[0]
            if value[-1] != quote:
                raise ValueError
            value = value[1:-1]
        elif value and value[0] in "\\"'":
            raise ValueError
        else:
            if "#" in value:
                value = value.split("#", 1)[0].strip()
        if key in result:
            raise ValueError
        result[key] = value
    return result
''',
    r'''assert parse_config("") == {}
assert parse_config("# comment\n\n") == {}
assert parse_config("a=1\nb = two \n") == {"a": "1", "b": "two"}
assert parse_config('a="x # y"') == {"a": "x # y"}
assert parse_config('a=" x "') == {"a": " x "}
assert parse_config("a=1 # note") == {"a": "1"}
assert parse_config("a= 1#note") == {"a": "1"}
assert parse_config("a='x=y'") == {"a": "x=y"}
assert parse_config('a=""') == {"a": ""}
assert list(parse_config("b=1\na=2")) == ["b", "a"]
assert parse_config("a=\n") == {"a": ""}
for bad in ["a", "a=1\na=2", " a b=1", 'a="x', 'a="', 'a="x" # note', "a='x\"", "=1", None]:
    try:
        parse_config(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(bad)
''',
    "        value = value.strip()\n        if len(value) >= 2 and value[0] in \"\\\"'\":",
    "        value = value.strip().split(\"#\", 1)[0].strip()\n        if len(value) >= 2 and value[0] in \"\\\"'\":",
)

add(
    "I008",
    "group_consecutive",
    """实现 group_consecutive(values)，把整数序列合并成连续区间的列表。values 必须是 list 或 tuple，元素必须是 int（bool 不合法），否则抛 ValueError。
返回按升序排列的 (start, end) 元组列表（闭区间），每个区间内的整数在输入中出现过，相邻整数合并为一个区间，重复值只算一次。空输入返回 []。例如 group_consecutive([3, 1, 2, 7]) == [(1, 3), (7, 7)]。""",
    '''def group_consecutive(values):
    if not isinstance(values, (list, tuple)):
        raise ValueError
    numbers = set()
    for value in values:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError
        numbers.add(value)
    result = []
    for number in sorted(numbers):
        if result and number == result[-1][1] + 1:
            result[-1] = (result[-1][0], number)
        else:
            result.append((number, number))
    return result
''',
    r'''assert group_consecutive([]) == []
assert group_consecutive([1]) == [(1, 1)]
assert group_consecutive([3, 1, 2, 7]) == [(1, 3), (7, 7)]
assert group_consecutive([1, 1, 1, 2]) == [(1, 2)]
assert group_consecutive([1, 1, 2, 2, 3]) == [(1, 3)]
assert group_consecutive([-3, -2, -1, 0]) == [(-3, 0)]
assert group_consecutive([0, 2, 4]) == [(0, 0), (2, 2), (4, 4)]
assert group_consecutive((5, 4, 6, 10, 11)) == [(4, 6), (10, 11)]
assert group_consecutive([100, 99]) == [(99, 100)]
for bad in [("1", "2"), [1.0], [True], "abc", {1: 2}]:
    try:
        group_consecutive(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(bad)
''',
    "        if result and number == result[-1][1] + 1:",
    "        if result and number == result[-1][1]:",
)


def add_mutant(task_id, name, old, new):
    task = next(item for item in TASKS if item["id"] == task_id)
    assert old in task["reference"], (task_id, old)
    assert task["reference"].count(old) == 1, (task_id, old)
    task["mutants"].append(dict(name=name, code=task["reference"].replace(old, new)))


add_mutant("I001", "余数按权重而非余数排序",
           "order = sorted(range(len(weights)), key=lambda i: (-(amount * weights[i] % total), i))",
           "order = sorted(range(len(weights)), key=lambda i: (-weights[i], i))")
add_mutant("I002", "混合标识符按字符串序比较",
           "            elif left_num != right_num:\n                return -1 if left_num else 1",
           "            elif left_num != right_num:\n                return -1 if left < right else 1")
add_mutant("I003", "星号至少匹配一个字符",
           '            return match(p[1:], t) or (bool(t) and t[0] != "/" and match(p, t[1:]))',
           '            return bool(t) and t[0] != "/" and match(p, t[1:])')
add_mutant("I005", "清空输入列表",
           "    order = []",
           "    events.clear()\n    order = []")
add_mutant("I006", "补充后不封顶",
           "        credit = min(capacity * 1000, credit + (timestamp - last) * refill)",
           "        credit = credit + (timestamp - last) * refill")


if __name__ == "__main__":
    path = Path(__file__).with_name("tasks.json")
    path.write_text(json.dumps(TASKS, ensure_ascii=False, indent=2) + "\n")
    print(f"已写入 {len(TASKS)} 个任务")
