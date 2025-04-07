import os
import json
import time
import hashlib
import requests
import srt
from tkinter import Tk, filedialog
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict

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
    def __init__(self, appid: str, secret_key: str, max_workers: int = 10, max_retries: int = 3) -> None:
        self.appid = appid
        self.secret_key = secret_key
        self.max_workers = max_workers
        self.max_retries = max_retries
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
                    return result["trans_result"][0]["dst"]
            except Exception:
                time.sleep(0.3)
        return ""

    def translate_srt_file(self, input_file: str, output_file: str, target_lang: str = "zh") -> None:
        """翻译 SRT 文件"""
        with open(input_file, "r", encoding="utf-8") as f:
            subtitles = list(srt.parse(f.read()))

        texts = [sub.content for sub in subtitles]
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            translated_texts = list(executor.map(lambda text: self.baidu_translate(text, to_lang=target_lang), texts))

        for sub, translated in zip(subtitles, translated_texts):
            sub.content = f"{sub.content}\n{translated}"

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(srt.compose(subtitles))

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

    translator = SRTTranslator(appid=config["appid"], secret_key=config["secret_key"], max_workers=10)
    input_file = select_file()
    if not input_file:
        print("未选择文件，程序退出。")
        return

    output_file = os.path.splitext(input_file)[0] + ".translated.srt"
    translator.translate_srt_file(input_file, output_file)
    print(f"翻译完成，输出文件：{output_file}")

if __name__ == "__main__":
    main()
