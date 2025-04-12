#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
该脚本实现 SRT 字幕翻译，支持逐条翻译和批量翻译模式，并保证原始 SRT 的时间轴和字幕编号不被翻译。

全局配置参数：
  OUTPUT_MODE: 0 表示输出双语字幕（原文 + 翻译），1 表示仅输出翻译字幕。
  TRANSLATION_MODE: 0 表示批量翻译，1 表示逐条翻译。
  
配置文件 translate_srt_config.json 中存储百度翻译 API 的 APP ID 与密钥。
"""

import re
import os
import sys
import json
import time
import hashlib
import random
import requests
import tkinter as tk
from tkinter import filedialog
import concurrent.futures

# --------------------------
# 全局配置参数
# --------------------------
OUTPUT_MODE = 0   # 0: 双语字幕输出（原文 + 翻译），1: 仅翻译字幕输出
TRANSLATION_MODE = 0   # 0: 批量翻译，1: 逐条翻译

CONFIG_FILENAME = "translate_srt_config.json"
BAIDU_URL = "https://fanyi-api.baidu.com/api/trans/vip/translate"

# --------------------------
# 1. 配置文件加载与保存
# --------------------------
def load_config():
    if not os.path.exists(CONFIG_FILENAME):
        print("未检测到配置文件。")
        appid = input("请输入百度翻译 API 的 APP ID: ").strip()
        secret = input("请输入百度翻译 API 的密钥: ").strip()
        config = {"appid": appid, "secret": secret}
        with open(CONFIG_FILENAME, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=4)
        print("配置已保存到", CONFIG_FILENAME)
    else:
        with open(CONFIG_FILENAME, "r", encoding="utf-8") as f:
            config = json.load(f)
        if "secret" not in config:
            if "secret_key" in config:
                config["secret"] = config["secret_key"]
            else:
                print("配置文件格式有误，请检查是否包含 'appid' 与 'secret' 键。")
                sys.exit(1)
    return config

# --------------------------
# 2. 文件选择：支持命令行参数或弹窗
# --------------------------
def select_file():
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        if os.path.exists(file_path):
            return file_path
        else:
            print("命令行指定的文件不存在:", file_path)
    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.askopenfilename(title="请选择字幕文件", filetypes=[("SRT字幕", "*.srt"), ("所有文件", "*.*")])
    return file_path

# --------------------------
# 辅助函数：判断哪些行需要翻译
# --------------------------
def should_translate(line):
    line = line.strip()
    if not line:
        return False
    # 如果该行只包含数字，则为字幕编号
    if line.isdigit():
        return False
    # 如果该行匹配时间轴格式，如 "00:01:20,720 --> 00:01:22,740"
    pattern = re.compile(r'^\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}$')
    if pattern.match(line):
        return False
    return True

# --------------------------
# 辅助函数：生成 MD5 签名
# --------------------------
def md5(string_input):
    m = hashlib.md5()
    m.update(string_input.encode("utf-8"))
    return m.hexdigest()

# --------------------------
# 百度翻译请求函数（含重试机制）
# --------------------------
def baidu_translate(text, config, from_lang="auto", to_lang="zh"):
    appid = config["appid"]
    secret = config["secret"]
    salt = str(random.randint(10000, 99999))
    sign = md5(appid + text + salt + secret)
    params = {
        "q": text,
        "from": from_lang,
        "to": to_lang,
        "appid": appid,
        "salt": salt,
        "sign": sign,
    }
    delay = 0.1
    max_delay = 1.0
    while True:
        try:
            response = requests.get(BAIDU_URL, params=params, timeout=5)
        except Exception as e:
            print(f"请求异常: {e}，等待 {delay:.1f}s 后重试…")
            time.sleep(delay)
            delay = min(delay + 0.1, max_delay)
            continue
        if response.status_code != 200:
            print(f"HTTP错误 {response.status_code}，等待 {delay:.1f}s 后重试…")
            time.sleep(delay)
            delay = min(delay + 0.1, max_delay)
            continue
        result = response.json()
        if "error_code" in result:
            print(f"API返回错误: {result.get('error_msg')}，等待 {delay:.1f}s 后重试…")
            time.sleep(delay)
            delay = min(delay + 0.1, max_delay)
            continue
        if "trans_result" in result:
            translations = [item["dst"] for item in result["trans_result"]]
            return "\n".join(translations)
        else:
            print(f"未知错误，等待 {delay:.1f}s 后重试…")
            time.sleep(delay)
            delay = min(delay + 0.1, max_delay)

# --------------------------
# 3. 逐条翻译模式：对每一行进行处理（翻译实际文本，其它行保持原样）
# --------------------------
def translate_srt_line_by_line(srt_lines, config):
    new_lines = []
    for line in srt_lines:
        if should_translate(line):
            trans_line = baidu_translate(line, config, from_lang="auto", to_lang="zh")
            new_lines.append(trans_line + "\n")
            print(f"翻译: {line.strip()} -> {trans_line.strip()}")
        else:
            new_lines.append(line)
    return new_lines

# --------------------------
# 4. 批量翻译模式：仅对需要翻译的行进行批量翻译，然后再填回原位置
# --------------------------
def translate_srt_in_batches(srt_lines, config, limit=6000):
    # 收集需要翻译的行的原始索引和文本（去除末尾换行符）
    indices = []
    texts = []
    for i, line in enumerate(srt_lines):
        if should_translate(line):
            indices.append(i)
            texts.append(line.rstrip("\n"))
    # 建立翻译结果列表，与 texts 长度相同
    translated_texts = [None] * len(texts)
    delimiter = "\n"
    # 分批：保证每批文本拼接后的字符数不超过 limit
    batches = []
    current_batch = []
    current_batch_idxs = []  # 注意：是 texts 列表中的索引
    current_len = 0
    for i, text in enumerate(texts):
        text_len = len(text)
        if current_batch and current_len + len(delimiter) + text_len > limit:
            batches.append((current_batch_idxs, current_batch))
            current_batch = [text]
            current_batch_idxs = [i]
            current_len = text_len
        else:
            current_batch.append(text)
            current_batch_idxs.append(i)
            current_len += (len(delimiter) if current_batch else 0) + text_len
    if current_batch:
        batches.append((current_batch_idxs, current_batch))
    # 对每批文本调用翻译接口
    for batch_idxs, batch_texts in batches:
        concat_text = delimiter.join(batch_texts)
        trans_result = baidu_translate(concat_text, config, from_lang="auto", to_lang="zh")
        split_trans = trans_result.split(delimiter)
        if len(split_trans) != len(batch_texts):
            print("【警告】 批量翻译后返回的行数与原文本行数不匹配!")
        for j, idx in enumerate(batch_idxs):
            translated_texts[idx] = split_trans[j] if j < len(split_trans) else ""
    # 构造新的 srt 文件行：将翻译结果填回原始索引位置
    new_lines = list(srt_lines)
    for order, i in enumerate(indices):
        new_lines[i] = translated_texts[order] + "\n"
    return new_lines

# --------------------------
# 5. 主程序入口
# --------------------------
def main():
    config = load_config()
    file_path = select_file()
    if not file_path:
        print("未选择文件，程序退出。")
        return
    with open(file_path, "r", encoding="utf-8") as f:
        srt_lines = f.readlines()
    print(f"读取文件：共 {len(srt_lines)} 行。")
    
    if TRANSLATION_MODE == 1:
        new_lines = translate_srt_line_by_line(srt_lines, config)
    elif TRANSLATION_MODE == 0:
        new_lines = translate_srt_in_batches(srt_lines, config)
    else:
        print("无效的翻译模式设置。")
        return
    
    # 生成输出文件：输出文件名在原文件名基础上增加 .chs
    base, ext = os.path.splitext(file_path)
    output_path = base + ".chs" + ext
    
    with open(output_path, "w", encoding="utf-8") as f:
        if OUTPUT_MODE == 0:
            # 双语字幕输出：对需要翻译的行，先输出原文，再输出翻译；其它行直接输出
            i = 0
            while i < len(srt_lines):
                if should_translate(srt_lines[i]):
                    f.write(srt_lines[i])
                    f.write(new_lines[i])
                    f.write("\n")
                else:
                    f.write(srt_lines[i])
                i += 1
        elif OUTPUT_MODE == 1:
            # 仅输出翻译结果（对需要翻译的行使用翻译文本，其它行原样输出）
            f.writelines(new_lines)
        else:
            print("无效的输出模式设置。")
            return
    print("翻译完成！输出文件为：", output_path)

if __name__ == "__main__":
    main()