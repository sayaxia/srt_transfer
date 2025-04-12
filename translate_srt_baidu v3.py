#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
该脚本实现：
1. 检查配置文件（保存百度翻译 API 的 APP ID 与密钥），若配置文件不存在则提示输入，配置文件名称为 translate_srt_config.json。
2. 支持拖拽文件、命令行参数或弹窗选择字幕文件。
3. 逐条翻译模式：使用线程池实现并行翻译，每条字幕翻译前先压缩连续重复字段。
4. 批量翻译模式：先对字幕进行压缩后，用换行符 ("\n") 拼接字幕，保证整体字符数不超过6000，再调用翻译接口，最后根据换行符拆分还原。
5. 请求节流：全局每秒最多发送10次请求；如果请求失败，则按 0.1 秒步进增加重试等待时间（最多5秒）。
6. 连续重复字段压缩：当一条字幕中有 3 次及以上连续相同单词时，仅保留前三次并追加省略号。
7. 根据配置决定输出格式和翻译模式：
   - 输出模式 (OUTPUT_MODE)： 0 表示输出双语字幕（原文 + 翻译），1 表示仅输出翻译结果；
   - 翻译模式 (TRANSLATION_MODE)： 0 表示使用批量翻译，1 表示使用逐条翻译。
"""

import os
import sys
import json
import time
import hashlib
import random
import threading
import requests
import tkinter as tk
from tkinter import filedialog
import concurrent.futures

# --------------------------
# 配置自动参数（0和1代表两种选择结果）
# --------------------------
# OUTPUT_MODE: 0 => 双语字幕输出（原文及翻译），1 => 仅输出翻译字幕。
OUTPUT_MODE = 0  
# TRANSLATION_MODE: 0 => 批量翻译，1 => 逐条翻译。
TRANSLATION_MODE = 0   

CONFIG_FILENAME = "translate_srt_config.json"
BAIDU_URL = "https://fanyi-api.baidu.com/api/trans/vip/translate"

# --------------------------
# 1. 配置文件加载与保存
# --------------------------
def load_config():
    """
    检查是否存在配置文件 translate_srt_config.json，
    如果不存在则提示用户输入 APP ID 与密钥并保存；
    如果配置文件中使用了 "secret_key" 而非 "secret"，自动补充。
    """
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
# 2. 文件选择：命令行、拖拽或弹窗
# --------------------------
def select_file():
    """
    优先使用命令行参数指定的文件路径，否则弹出 Tkinter 文件选择对话框。
    """
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        if os.path.exists(file_path):
            return file_path
        else:
            print("命令行指定的文件不存在:", file_path)
    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.askopenfilename(title="请选择字幕文件")
    return file_path

# --------------------------
# 6. 连续重复字段压缩函数
# --------------------------
def compress_line(line):
    """
    如果一条字幕中有 3 次及以上连续重复的单词，则压缩为前三个重复加上省略号。
    例如："ABC ABC ABC ABC ABC ABC" 转换成 "ABC ABC ABC ..."
    """
    words = line.split()
    if not words:
        return line
    new_words = []
    i = 0
    while i < len(words):
        count = 1
        while i + count < len(words) and words[i + count] == words[i]:
            count += 1
        if count >= 3:
            new_words.extend(words[i:i+3])
            new_words.append("...")
            i += count
        else:
            new_words.extend(words[i:i+count])
            i += count
    return " ".join(new_words)

# --------------------------
# 辅助函数：生成 MD5 签名
# --------------------------
def md5(string_input):
    m = hashlib.md5()
    m.update(string_input.encode("utf-8"))
    return m.hexdigest()

# --------------------------
# 5. 请求速率控制
# --------------------------
# 全局令牌桶，每秒允许最多 10 个请求
rate_semaphore = threading.Semaphore(10)

def replenish_tokens():
    """
    每秒补充10个令牌。
    """
    while True:
        time.sleep(1)
        for _ in range(10):
            rate_semaphore.release()

# 启动补充令牌的守护线程
replenish_thread = threading.Thread(target=replenish_tokens, daemon=True)
replenish_thread.start()

# --------------------------
# 百度翻译请求函数（支持重试与延时调整）
# --------------------------
def baidu_translate(text, config, from_lang="auto", to_lang="zh"):
    """
    利用百度翻译 API 翻译给定文本。
    使用全局令牌控制请求速率，若请求失败则以 0.1 秒步进增加延时（最高 1 秒后重试）。
    """
    appid = config["appid"]
    secret = config["secret"]
    salt = str(random.randint(10000, 99999))
    sign_str = appid + text + salt + secret
    sign = md5(sign_str)
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
        rate_semaphore.acquire()
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
            print(f"API 返回错误: {result.get('error_msg')}，等待 {delay:.1f}s 后重试…")
            time.sleep(delay)
            delay = min(delay + 0.1, max_delay)
            continue

        if "trans_result" in result:
            # 拼接多条翻译结果时用换行符分隔，每一行对应原始一条文本
            translation = "\n".join(item["dst"] for item in result["trans_result"])
            return translation
        else:
            print(f"未知错误，等待 {delay:.1f}s 后重试…")
            time.sleep(delay)
            delay = min(delay + 0.1, max_delay)

# --------------------------
# 辅助函数：将字幕列表分批，保证拼接后整体字符数（含换行符）不超过 limit
# --------------------------
def batch_subtitles(subtitles, limit=6000, delimiter="\n"):
    """
    根据整体字符数限制，将字幕列表分批，每批拼接后的整体字符数不超过 limit。
    其中使用换行符作为分隔符。
    """
    batches = []
    current_batch = []
    current_len = 0
    delim_len = len(delimiter)
    for s in subtitles:
        s_len = len(s)
        if current_batch:
            if current_len + delim_len + s_len > limit:
                batches.append(current_batch)
                current_batch = [s]
                current_len = s_len
            else:
                current_batch.append(s)
                current_len += delim_len + s_len
        else:
            current_batch.append(s)
            current_len = s_len
    if current_batch:
        batches.append(current_batch)
    return batches

# --------------------------
# 3. 逐条翻译模式（并行请求）
# --------------------------
def translate_line_by_line(subtitles, config, from_lang="auto", to_lang="zh"):
    """
    对每条字幕进行逐条翻译，采用多线程并行请求。
    每条字幕翻译前先进行连续重复字段压缩。
    """
    results = [None] * len(subtitles)

    def worker(i, line):
        compressed_line = compress_line(line)
        translation = baidu_translate(compressed_line, config, from_lang, to_lang)
        results[i] = translation
        print(f"翻译完成【{i + 1}/{len(subtitles)}】")

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(worker, i, line): i for i, line in enumerate(subtitles)}
        concurrent.futures.wait(futures.keys())
    return results

# --------------------------
# 4. 批量翻译模式（按批分割字幕，且整体字符数不超过6000）
# --------------------------
def translate_in_batches(subtitles, config, from_lang="auto", to_lang="zh", limit=6000):
    """
    批量翻译字幕：
    1. 每条字幕先进行连续重复字段压缩处理；
    2. 使用换行符 ("\n") 拼接后，确保请求文本整体字符数不超过 limit；
    3. 分批调用百度翻译接口，再根据换行符拆分还原每条翻译结果；
    4. 显示当前批次处理的字幕条数范围以及总条数。
    """
    delimiter = "\n"
    compressed_subtitles = [compress_line(line) for line in subtitles]
    total = len(compressed_subtitles)
    batches = batch_subtitles(compressed_subtitles, limit, delimiter)
    results = []
    processed = 0  # 已处理字幕数
    for batch in batches:
        text_to_translate = delimiter.join(batch)
        translation_text = baidu_translate(text_to_translate, config, from_lang, to_lang)
        translated_lines = translation_text.split(delimiter)
        if len(translated_lines) != len(batch):
            print("【警告】 批量翻译后返回的行数与原始行数不匹配!")
        results.extend(translated_lines)
        start_index = processed + 1
        end_index = processed + len(batch)
        print(f"批量翻译完成【{start_index}-{end_index}/{total}】")
        processed += len(batch)
    return results

# --------------------------
# 主程序入口
# --------------------------
def main():
    config = load_config()
    file_path = select_file()
    if not file_path:
        print("未选择文件，程序退出。")
        return

    # 假设字幕文件每行代表一条字幕
    with open(file_path, "r", encoding="utf-8") as f:
        subtitles = [line.strip() for line in f if line.strip()]

    if not subtitles:
        print("文件为空，程序退出。")
        return

    print(f"共检测到 {len(subtitles)} 条字幕。")

    # 根据 TRANSLATION_MODE 参数选择翻译模式：
    #   0 => 批量翻译, 1 => 逐条翻译
    if TRANSLATION_MODE == 0:
        translated = translate_in_batches(subtitles, config)
    elif TRANSLATION_MODE == 1:
        translated = translate_line_by_line(subtitles, config)
    else:
        print("无效的翻译模式设置，程序退出。")
        return

    # 输出文件名格式修改为 “原文件名.chs.srt”
    base, ext = os.path.splitext(file_path)
    output_path = base + ".chs" + ext

    # 根据 OUTPUT_MODE 参数决定输出格式：
    #   0 => 双语字幕（原文 + 翻译），1 => 仅翻译字幕
    if OUTPUT_MODE == 0:
        with open(output_path, "w", encoding="utf-8") as f:
            for original_line, translation_line in zip(subtitles, translated):
                f.write(original_line + "\n")
                f.write(translation_line + "\n")
                f.write("\n")  # 每个字幕块后添加空行作为分隔
    elif OUTPUT_MODE == 1:
        with open(output_path, "w", encoding="utf-8") as f:
            for translation_line in translated:
                f.write(translation_line + "\n")
    else:
        print("无效的输出模式设置，程序退出。")
        return

    print("翻译完成！输出文件为：", output_path)

if __name__ == "__main__":
    main()