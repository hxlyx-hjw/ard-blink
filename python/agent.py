#!/usr/bin/env python3
"""基于 DeepSeek 的传感器学习助手（Agent）。

能力：列出记录、分析记录、设置标准数据、与标准比对给建议、绘图、串口录制。

用法：
  set DEEPSEEK_API_KEY=sk-xxxx
  python agent.py "分析最新记录并给建议"
  python agent.py "把 rec_xxx 设为标准，再和最新记录比对"
  python agent.py               # 进入交互模式
"""

import argparse
import json
import os
import sys

import sensor_lib as lib


MODEL = "deepseek-chat"
BASE_URL = "https://api.deepseek.com"
MAX_STEPS = 8

SYSTEM_PROMPT = (
    "你是基于 DeepSeek 的传感器学习助手，帮助用户分析 8 路光电循迹 + IMU660RB 的记录数据。"
    "可调用工具：list_recordings、analyze_recording、set_standard、get_standard、"
    "compare_with_standard、compare_with_template、plot_recordings、record_session，"
    "以及 skill 库工具 list_skills、save_skill、remove_skill、learn_template，"
    "和文件工具 list_files、read_file、write_file、show_file。"
    "skill 库是用户录下来的标准（学习完成）曲线库，learn_template 会把它聚合成均值±标准差模板，"
    "compare_with_template 用新记录与模板比对给出相似度与建议。"
    "回答用中文，先给结论，再给依据和可操作建议；所有数值必须来自工具返回，不要编造。"
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_recordings",
            "description": "列出 recordings 目录里所有记录文件及基本信息",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_standard",
            "description": "把某次记录设为标准（学习完成后的参考数据）",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "记录文件名，或 latest"}
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_standard",
            "description": "查询当前标准数据是哪一次记录",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_recording",
            "description": "分析一次记录，返回统计指标",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "记录文件名，或 latest"}
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_with_standard",
            "description": "把某次记录与标准数据比对，返回差异、相似度与建议",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "记录文件名，或 latest"}
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plot_recordings",
            "description": "把指定记录画成完整图形并保存为 PNG",
            "parameters": {
                "type": "object",
                "properties": {
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "记录文件名列表",
                    }
                },
                "required": ["names"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_session",
            "description": "从串口录制一段新数据（秒），保存 CSV 与图并返回统计指标",
            "parameters": {
                "type": "object",
                "properties": {
                    "port": {"type": "string", "description": "串口号，如 COM5"},
                    "seconds": {"type": "number", "description": "录制时长（秒）"},
                },
                "required": ["port", "seconds"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "列出 skill 库里的全部技能（已录入的学习曲线）",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_skill",
            "description": "把某次记录存入 skill 库，成为学习模板之一",
            "parameters": {
                "type": "object",
                "properties": {
                    "recording": {"type": "string", "description": "记录文件名或 latest"},
                    "skill_name": {"type": "string", "description": "技能名称"},
                    "note": {"type": "string", "description": "备注（可选）"},
                },
                "required": ["recording", "skill_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_skill",
            "description": "从 skill 库删除一个技能",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "learn_template",
            "description": "统计 skill 库全部曲线，聚合成均值±标准差模板",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "只统计这些技能；缺省则统计全部",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_with_template",
            "description": "把某次记录与 skill 库聚合模板比对，返回偏差、相似度与建议",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "记录文件名或 latest"}
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出 python 目录（recordings/skills 等）里的文件",
            "parameters": {
                "type": "object",
                "properties": {"subdir": {"type": "string", "description": "子目录，如 recordings"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文本文件内容（CSV/JSON 等）",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "把文本内容写入文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_file",
            "description": "用系统默认程序打开文件直接展示（如 PNG 图、CSV）",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
]


def call_tool(name, args):
    try:
        if name == "list_recordings":
            return json.dumps(lib.list_recordings(), ensure_ascii=False)
        if name == "set_standard":
            return json.dumps({"standard": lib.set_standard(args["name"])}, ensure_ascii=False)
        if name == "get_standard":
            return json.dumps({"standard": lib.get_standard_name()}, ensure_ascii=False)
        if name == "analyze_recording":
            p = lib.resolve(args["name"])
            m = lib.compute_metrics(lib.load_recording(p))
            return json.dumps(
                {"recording": os.path.basename(p), "metrics": m}, ensure_ascii=False
            )
        if name == "compare_with_standard":
            return json.dumps(lib.compare_with_standard(args["name"]), ensure_ascii=False)
        if name == "plot_recordings":
            pngs = []
            for n in args["names"]:
                p = lib.resolve(n)
                pngs.append(lib.plot_recording(p, out_png=p + ".png"))
            return json.dumps({"plots": pngs}, ensure_ascii=False)
        if name == "record_session":
            seconds = float(args.get("seconds", 10))
            p = lib.record_headless(args["port"], duration=seconds, prefix="agent")
            m = lib.compute_metrics(lib.load_recording(p))
            lib.plot_recording(p, out_png=p + ".png")
            return json.dumps(
                {"recording": os.path.basename(p), "metrics": m}, ensure_ascii=False
            )
        if name == "list_skills":
            return json.dumps(lib.list_skills(), ensure_ascii=False)
        if name == "save_skill":
            skill = lib.save_skill(
                args["recording"], args["skill_name"], args.get("note", "")
            )
            return json.dumps({"skill": skill}, ensure_ascii=False)
        if name == "remove_skill":
            return json.dumps({"removed": lib.remove_skill(args["name"])}, ensure_ascii=False)
        if name == "learn_template":
            tpl = lib.build_template(args.get("skill_names") or None)
            return json.dumps(
                {"skills": tpl["count"], "n_points": tpl["n_points"],
                 "channels": lib.TEMPLATE_CHANNELS},
                ensure_ascii=False,
            )
        if name == "compare_with_template":
            return json.dumps(lib.compare_with_template(args["name"]), ensure_ascii=False)
        if name == "list_files":
            return json.dumps(lib.list_files(args.get("subdir", "")), ensure_ascii=False)
        if name == "read_file":
            return lib.read_file(args["path"])
        if name == "write_file":
            return json.dumps(
                {"written": lib.write_file(args["path"], args["content"])},
                ensure_ascii=False,
            )
        if name == "show_file":
            return json.dumps({"shown": lib.show_file(args["path"])}, ensure_ascii=False)
        return json.dumps({"error": f"未知工具 {name}"}, ensure_ascii=False)
    except Exception as e:  # 工具异常要返回给模型，让模型解释
        return json.dumps({"error": str(e)}, ensure_ascii=False)


class Agent:
    def __init__(self):
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("未设置环境变量 DEEPSEEK_API_KEY")
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=BASE_URL)

    def chat(self, prompt):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        for _ in range(MAX_STEPS):
            resp = self.client.chat.completions.create(
                model=MODEL, messages=messages, tools=TOOLS, tool_choice="auto"
            )
            msg = resp.choices[0].message
            if not msg.tool_calls:
                return msg.content

            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                result = call_tool(tc.function.name, args)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result}
                )
        return "已达到最大工具调用轮数，请把问题拆小一点再问。"


def main():
    ap = argparse.ArgumentParser(description="DeepSeek 传感器学习助手")
    ap.add_argument("prompt", nargs="*", help="问题；不填则进入交互模式")
    args = ap.parse_args()

    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("请先设置环境变量：set DEEPSEEK_API_KEY=sk-xxxx")
        sys.exit(1)

    agent = Agent()
    if args.prompt:
        print(agent.chat(" ".join(args.prompt)))
        return

    print("输入问题后回车，Ctrl+C 退出。")
    while True:
        try:
            q = input(">> ")
        except (EOFError, KeyboardInterrupt):
            break
        if not q.strip():
            continue
        print(agent.chat(q))


if __name__ == "__main__":
    main()
