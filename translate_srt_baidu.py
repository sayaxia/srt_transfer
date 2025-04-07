import os
import json
import time
import hashlib
import requests
import srt
from tkinter import Tk, filedialog
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict
import threading

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "translate_srt_config.json")

def load_config(config_file: str) -> Dict[str, str]:
    """加载配置文件"""
    if os.path.exists(config_file):
        with open(config_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_config(config: Dict[str, str], config_file: str) -> None:
    """保存配置文件"""
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

class SRTTranslator:
    def __init__(self, appid: str, secret_key: str, max_workers: int = 10, max_retries: int = 3, batch_mode: bool = False, batch_size: int = 5) -> None:
        self.appid = appid
        self.secret_key = secret_key
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.batch_mode = batch_mode
        self.batch_size = batch_size
        self.session = requests.Session()

    def split_text(self, text: str, max_bytes: int = 5800) -> List[str]:
        """按字节数拆分文本"""
        parts, current = [], 0
        while current < len(text):
            end = current
            while end < len(text) and len(text[current:end + 1].encode("utf-8")) <= max_bytes:
                end += 1
            parts.append(text[current:end])
            current = end
        return parts

    def baidu_translate(self, text: str, from_lang: str = "auto", to_lang: str = "zh") -> str:
        """调用百度翻译 API 翻译文本"""
        if len(text.encode("utf-8")) > 5800:
            parts = self.split_text(text)
            return "".join(self.baidu_translate(part, from_lang, to_lang) for part in parts)

        base_url = "https://fanyi-api.baidu.com/api/trans/vip/translate"
        salt = str(int(time.time() * 1000))
        sign = hashlib.md5((self.appid + text + salt + self.secret_key).encode("utf-8")).hexdigest()
        params = {"q": text, "from": from_lang, "to": to_lang, "appid": self.appid, "salt": salt, "sign": sign}

        for _ in range(self.max_retries):
            try:
                response = self.session.get(base_url, params=params, timeout=10)
                response.raise_for_status()
                result = response.json()
                if "trans_result" in result:
                    # 限制请求频率为 QPS=10（每次请求间隔至少 0.2 秒）
                    time.sleep(0.2)
                    return result["trans_result"][0]["dst"]
            except Exception as e:
                print(f"翻译请求失败：{e}")
                time.sleep(0.3)  # 如果失败，稍作延迟后重试
        return ""

    def translate_srt_file(self, input_file: str, output_file: str, target_lang: str = "zh") -> None:
        """翻译 SRT 文件"""
        print(f"正在翻译文件：{input_file}")
        with open(input_file, "r", encoding="utf-8") as f:
            subtitles = list(srt.parse(f.read()))

        texts = [sub.content for sub in subtitles]
        print(f"共 {len(texts)} 条字幕需要翻译...")

        if self.batch_mode:
            # 一次性翻译多条字幕
            print(f"启用批量翻译模式，每次翻译 {self.batch_size} 条字幕...")
            translated_texts = []
            for i in range(0, len(texts), self.batch_size):
                batch = texts[i:i + self.batch_size]
                batch_text = "\n".join(batch)
                translated_batch = self.baidu_translate(batch_text, to_lang=target_lang)
                translated_texts.extend(translated_batch.split("\n"))
                print(f"已翻译第 {i + 1} 至 {min(i + self.batch_size, len(texts))} 条字幕")
        else:
            # 逐条翻译
            def log_translation(index, text):
                translated = self.baidu_translate(text, to_lang=target_lang)
                print(f"线程 {threading.current_thread().name} - 第 {index + 1} 条字幕翻译完成：{text} -> {translated}")
                return translated

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                translated_texts = list(executor.map(lambda args: log_translation(*args), enumerate(texts)))

        for sub, translated in zip(subtitles, translated_texts):
            sub.content = f"{sub.content}\n{translated}"

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(srt.compose(subtitles))
        print(f"翻译完成，已保存到：{output_file}")

def select_file() -> str:
    """弹窗选择文件"""
    root = Tk()
    root.withdraw()  # 隐藏主窗口
    file_path = filedialog.askopenfilename(
        title="选择 SRT 文件",
        filetypes=[("SRT 文件", "*.srt")],
    )
    return file_path

def main() -> None:
    config = load_config(CONFIG_FILE)
    if not config.get("appid") or not config.get("secret_key"):
        config["appid"] = input("请输入百度翻译 API appid: ").strip()
        config["secret_key"] = input("请输入百度翻译 API 密钥: ").strip()
        save_config(config, CONFIG_FILE)

    translator = SRTTranslator(
        appid=config["appid"],
        secret_key=config["secret_key"],
        max_workers=10,
        batch_mode=True,  # 设置为 True 启用批量翻译
        batch_size=1000   # 每次翻译 1000 条字幕
    )
    input_file = select_file()
    if not input_file:
        print("未选择文件，程序退出。")
        return

    # 修改输出文件名为原文件名加上 .chs 后缀
    output_file = os.path.splitext(input_file)[0] + ".chs.srt"
    translator.translate_srt_file(input_file, output_file)
    print(f"翻译完成，输出文件：{output_file}")

if __name__ == "__main__":
    main()