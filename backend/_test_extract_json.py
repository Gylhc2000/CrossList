# -*- coding: utf-8 -*-
"""extract_json 加固后的单测：覆盖历史上出现过的 4 类失败样本。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.core.llm import extract_json, LlmError

cases = [
    # 1. 正常 JSON
    ('{"score": 92, "comments": "ok"}', 92),
    # 2. markdown 围栏包裹
    ('```json\n{"score": 88, "comments": "好"}\n```', 88),
    # 3. 字符串内裸换行（历史案例：解析失败主因）
    ('{\n  "score": 55,\n  "comments": "点评：语言地道且卖点清晰，但多处编造事实\n（续航、重量）",\n  "suggestions": []\n}', 55),
    # 4. BOM / 零宽字符开头（历史案例：报错后面「看起来是空的」）
    ('\ufeff{"score": 77, "comments": "bom"}', 77),
    ('\u200b\u200b{"score": 76, "comments": "zw"}', 76),
    # 5. <think> 思维链包裹
    ('<think>让我分析一下这个listing。</think>\n{"score": 81, "comments": "think"}', 81),
    # 6. 截断 JSON（补括号兜底）
    ('{"score": 90, "comments": "截断测试", "suggestions": ["建议一", "建议二', 90),
    # 7. 字符串内裸换行 + 截断同时出现
    ('{"score": 66, "comments": "换行\n加截断", "suggestions": ["修', 66),
    # 8. 前后杂讯
    ('好的，以下是评分结果：\n{"score": 70, "comments": "noise"}\n希望有帮助', 70),
]

ok = fail = 0
for i, (raw, want) in enumerate(cases, 1):
    try:
        obj = extract_json(raw)
        got = obj.get("score")
        status = "PASS" if got == want else f"FAIL(got {got})"
        if got == want: ok += 1
        else: fail += 1
    except LlmError as e:
        status = f"FAIL(异常: {e})"
        fail += 1
    print(f"case{i}: {status}")

# 空输入仍应报错（带「（空）」标记）
try:
    extract_json("")
    print("case-empty: FAIL(未报错)"); fail += 1
except LlmError as e:
    print(f"case-empty: PASS({e})"); ok += 1

print(f"\n{ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
