#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import json
import srt
import tkinter as tk
from tkinter import filedialog
import time
import hashlib
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

# 默认配置文件路径，与脚本同目录下
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "translate_srt_config.json")

def load_config(config_file: str) -> Dict[str, Any]:
    """读取配置文件，若存在则返回内容，否则返回空字典"""
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"读取配置文件出错：{e}")
    return {}

def save_config(config: Dict[str, Any], config_file: str) -> None:
    """将配置写入文件"""
    try:
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        print("配置已保存。")
    except Exception as e:
        print(f"保存配置文件出错：{e}")

class SRTTranslator:
    def __init__(
        self,
        appid: str,
        secret_key: str,
        bilingual_mode: bool = True,
        request_delay: float = 0.3,
        max_workers: int = 20,
        max_retries: int = 3,
    ) -> None:
        """
        初始化百度翻译 API 所需参数及其它控制参数

        :param appid: 百度翻译 API 的 appid
        :param secret_key: 百度翻译 API 的密钥
        :param bilingual_mode: 是否保留原文并追加译文（双语模式）
        :param request_delay: 每次请求后的延时（秒），防止请求过快
        :param max_workers: 并发翻译时线程池的工作线程数
        :param max_retries: 单条翻译失败时的重试次数
        """
        self.appid = appid
        self.secret_key = secret_key
        self.bilingual_mode = bilingual_mode
        self.request_delay = request_delay
        self.max_retries = max_retries
        self.max_workers = max_workers
        self.session = requests.Session()
        self.cache: Dict[str, str] = {}

    def split_text(self, text: str, max_bytes: int = 6000) -> List[str]:
        """
        将文本按 UTF-8 编码拆分成若干段，使得每段字节数不超过 max_bytes。
        此处采用逐字符累加的方式，由于 SRT字幕通常内容不长，性能开销可以接受。

        :param text: 待拆分文本
        :param max_bytes: 最大允许的字节数（默认为6000）
        :return: 拆分后文本列表
        """
        parts = []
        current = 0
        n = len(text)
        while current < n:
            end = current
            # 逐步增加 end ，直到超出 max_bytes
            while end < n and len(text[current:end+1].encode("utf-8")) <= max_bytes:
                end += 1
            # 如果没有任何字符满足（理论上不可能，因为单个字符一般都很小），至少取一个字符
            if end == current:
                end = current + 1
            parts.append(text[current:end])
            current = end
        return parts

    def baidu_translate(self, text: str, from_lang: str = "auto", to_lang: str = "zh") -> str:
        """
        调用百度翻译 API 对单个文本翻译，每次请求均独立生成签名。
        如果待翻译文本超过6000 bytes，则将文本拆分为多个小段分别翻译，
        最后拼接各部分译文返回。

        :param text: 待翻译文本
        :param from_lang: 源语言（默认自动检测）
        :param to_lang: 目标语言（默认 zh）
        :return: 翻译后的文本；失败时返回空字符串
        """
        if text in self.cache:
            return self.cache[text]
        # 判断文本是否超过6000 bytes
        if len(text.encode("utf-8")) > 6000:
            # 拆分文本并分别翻译
            parts = self.split_text(text, 6000)
            translated_parts = [self.baidu_translate(part, from_lang, to_lang) for part in parts]
            result = "".join(translated_parts)
            self.cache[text] = result
            return result

        base_url = "https://fanyi-api.baidu.com/api/trans/vip/translate"
        salt = str(int(time.time() * 1000))
        sign_raw = self.appid + text + salt + self.secret_key
        sign = hashlib.md5(sign_raw.encode("utf-8")).hexdigest()
        params = {
            "q": text,
            "from": from_lang,
            "to": to_lang,
            "appid": self.appid,
            "salt": salt,
            "sign": sign,
        }
        for attempt in range(self.max_retries):
            try:
                response = self.session.get(base_url, params=params, timeout=10)
                response.raise_for_status()
                result_json = response.json()
                if "trans_result" in result_json:
                    translated = result_json["trans_result"][0]["dst"]
                    self.cache[text] = translated
                    return translated
                else:
                    error_msg = result_json.get("error_msg", "未知错误")
                    print(f"翻译错误：{error_msg}。文本：{text}")
            except Exception as e:
                print(f"尝试 {attempt+1}/{self.max_retries} 出错：{e}")
            time.sleep(self.request_delay)
        return ""

    def parallel_translate(self, texts: List[str], from_lang: str = "auto", to_lang: str = "zh") -> List[str]:
        """
        利用线程池并行翻译文本列表，每个文本单独调用百度翻译接口，
        确保各自生成签名，保证不超过单条请求长度限制。

        :param texts: 待翻译文本列表
        :param from_lang: 源语言
        :param to_lang: 目标语言
        :return: 翻译后的文本列表（顺序与输入一致）
        """
        results = [None] * len(texts)
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_index = {executor.submit(self.baidu_translate, text, from_lang, to_lang): i for i, text in enumerate(texts)}
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    print(f"翻译索引 {idx} 出错：{e}")
                    results[idx] = ""
        return results

    def translate_srt_file(self, input_file: str, target_lang: str = "zh") -> str:
        """
        翻译整个 SRT 文件。读取文件后一次性收集所有字幕内容，
        利用并行方式逐条翻译（单条翻译内部支持超长文本拆分），
        然后根据 bilingual_mode 生成包含译文的新版 SRT 文件。

        :param input_file: 输入的 SRT 文件路径
        :param target_lang: 目标语言（默认 zh）
        :return: 输出文件路径（原名+ .chs.srt）
        """
        try:
            with open(input_file, "r", encoding="utf-8") as f:
                srt_data = f.read()
        except UnicodeDecodeError:
            with open(input_file, "r", encoding="gbk") as f:
                srt_data = f.read()

        subtitles: List[srt.Subtitle] = list(srt.parse(srt_data))
        total = len(subtitles)
        print(f"总字幕数：{total}")
        # 取每条字幕文本，如果为空则替换为一个空格（避免 API 拒绝空字符串）
        original_texts = [sub.content.strip() if sub.content.strip() else " " for sub in subtitles]
        
        # 并行翻译所有字幕内容
        translated_texts = self.parallel_translate(original_texts, to_lang=target_lang)
        
        # 更新字幕内容
        for idx in range(total):
            if self.bilingual_mode:
                subtitles[idx].content = f"{subtitles[idx].content}\n{translated_texts[idx]}"
            else:
                subtitles[idx].content = translated_texts[idx]
            print(f"处理完第 {idx+1}/{total} 条字幕")
        output_file = os.path.splitext(input_file)[0] + ".chs.srt"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(srt.compose(subtitles))
        print(f"\n翻译完成：\n  输入文件：{input_file}\n  输出文件：{output_file}")
        return output_file

    def select_files_via_dialog(self) -> List[str]:
        """
        弹出文件选择对话框，供用户选择 SRT 文件。
        如果支持拖拽，命令行参数也会作为文件路径处理。

        :return: 选择的 SRT 文件路径列表
        """
        root = tk.Tk()
        root.withdraw()
        file_paths = filedialog.askopenfilenames(
            title="请选择 SRT 字幕文件", filetypes=[("SRT 文件", "*.srt")]
        )
        return list(root.tk.splitlist(file_paths))

    def run(self) -> None:
        """
        主流程：若命令行参数中传入了文件（支持拖拽多个文件到脚本上），
        则直接处理；否则弹出文件对话框供用户选择 SRT 文件。
        """
        # 检查命令行参数（除了脚本名本身）
        if len(sys.argv) > 1:
            file_list = sys.argv[1:]
            print("通过命令行参数传入以下文件：")
            for f in file_list:
                print(f)
        else:
            print("请选择需要翻译的字幕文件...")
            file_list = self.select_files_via_dialog()
            if not file_list:
                print("未选择任何文件，程序退出。")
                return

        for file_path in file_list:
            if os.path.isfile(file_path) and file_path.lower().endswith(".srt"):
                try:
                    self.translate_srt_file(file_path)
                except Exception as e:
                    print(f"处理文件 '{file_path}' 时出错：{e}")
            else:
                print(f"跳过非 SRT 文件：{file_path}")
        print("所有文件处理完成，程序退出。")


def main() -> None:
    # 载入配置文件，如未配置则提示输入百度翻译 API 信息
    config = load_config(CONFIG_FILE)
    if not config.get("appid") or not config.get("secret_key"):
        config["appid"] = input("请输入您的百度翻译 API appid: ").strip()
        config["secret_key"] = input("请输入您的百度翻译 API 密钥: ").strip()
        save_config(config, CONFIG_FILE)
    else:
        print("配置文件加载成功。")
    translator = SRTTranslator(
        appid=config["appid"],
        secret_key=config["secret_key"],
        bilingual_mode=True,
        request_delay=0.3,
        max_workers=20,
        max_retries=3,
    )
    translator.run()

if __name__ == "__main__":
    main()
