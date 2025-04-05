#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import os
import json
import srt
import tkinter as tk
from tkinter import filedialog
import time
import deepl  # 请确保已安装 deepl 模块（pip install --upgrade deepl）

from typing import Any, Dict, List, Optional


class SRTTranslator:
    def __init__(
        self,
        bilingual_mode: bool = True,
        fallback_batch_size: int = 1000,
        request_delay: float = 0.3,
        config_file: Optional[str] = None,
    ) -> None:
        self.bilingual_mode: bool = bilingual_mode
        self.fallback_batch_size: int = fallback_batch_size
        self.request_delay: float = request_delay
        self.config_file: str = (
            config_file
            if config_file
            else os.path.join(os.path.dirname(os.path.abspath(__file__)), "translate_srt_config.json")
        )
        self.api_key: str = ""
        self.translator: Optional[deepl.Translator] = None  # 初始化后赋值

    def load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"读取配置文件出错：{e}")
        return {}

    def save_config(self, config: Dict[str, Any]) -> None:
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"保存配置文件出错：{e}")

    def ask_api_key(self) -> str:
        return input("请输入您的 DeepL API Key: ").strip()

    def init_translator(self) -> None:
        # 使用 deepl 模块的 Translator 对象进行 API 调用
        self.translator = deepl.Translator(self.api_key)

    def translate_texts(self, texts: List[str], target_lang: str = "ZH") -> List[str]:
        assert self.translator is not None, "translator 未初始化"
        try:
            # deepl 模块支持将文本列表一次性翻译，返回 Translation 对象列表
            result = self.translator.translate_text(texts, target_lang=target_lang)
            if isinstance(result, list):
                return [r.text for r in result]
            else:
                return [result.text]
        except Exception as e:
            raise e

    def translate_srt_file(self, input_file: str, target_lang: str = "ZH") -> str:
        try:
            with open(input_file, "r", encoding="utf-8") as f:
                srt_data: str = f.read()
        except UnicodeDecodeError:
            with open(input_file, "r", encoding="gbk") as f:
                srt_data = f.read()

        subtitles: List[srt.Subtitle] = list(srt.parse(srt_data))
        total: int = len(subtitles)
        print(f"总字幕数：{total}")
        # 若字幕内容为空，使用空格替代，确保 DeepL 接收到非空文本
        original_texts: List[str] = [sub.content if sub.content.strip() else " " for sub in subtitles]

        try:
            print("采用单批模式翻译...")
            translated_texts: List[str] = self.translate_texts(original_texts, target_lang=target_lang)
            for idx in range(total):
                if self.bilingual_mode:
                    subtitles[idx].content = f"{subtitles[idx].content}\n{translated_texts[idx]}"
                else:
                    subtitles[idx].content = translated_texts[idx]
            print(f"单批模式成功处理了 {total} 条字幕。")
        except Exception as e:
            print(f"\n单批模式出错：{e}")
            print("将尝试使用分批模式重试...")
            for i in range(0, total, self.fallback_batch_size):
                batch_texts: List[str] = original_texts[i: i + self.fallback_batch_size]
                try:
                    translated_batch: List[str] = self.translate_texts(batch_texts, target_lang=target_lang)
                except Exception as err:
                    raise Exception(f"处理字幕批次【{i+1} 到 {min(i+self.fallback_batch_size, total)}】时出错：{err}")
                for j, translated in enumerate(translated_batch):
                    if self.bilingual_mode:
                        subtitles[i + j].content = f"{subtitles[i + j].content}\n{translated}"
                    else:
                        subtitles[i + j].content = translated
                print(f"已处理字幕【{i+1} 到 {min(i+self.fallback_batch_size, total)}】")
                time.sleep(self.request_delay)

        output_file: str = os.path.splitext(input_file)[0] + ".chs.srt"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(srt.compose(subtitles))
        completion_message: str = "双语字幕处理完成" if self.bilingual_mode else "字幕处理完成"
        print(f"\n{completion_message}：\n  输入：{input_file}\n  输出：{output_file}")
        return output_file

    def select_files_via_dialog(self) -> List[str]:
        root = tk.Tk()
        root.withdraw()
        file_paths: List[str] = filedialog.askopenfilenames(
            title="请选择 SRT 字幕文件", filetypes=[("SRT 文件", "*.srt")]
        )
        return list(root.tk.splitlist(file_paths))

    def run(self) -> None:
        print("选择需要翻译的字幕文件...")
        config: Dict[str, Any] = self.load_config()
        self.api_key = config.get("api_key", "").strip()
        if not self.api_key:
            self.api_key = self.ask_api_key()
            config["api_key"] = self.api_key
            self.save_config(config)
        self.init_translator()
        print("设置：输出双语字幕 (默认)")

        file_list: List[str] = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
        if not file_list:
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
    translator = SRTTranslator(bilingual_mode=True, fallback_batch_size=1, request_delay=0.5)
    translator.run()


if __name__ == "__main__":
    main()
