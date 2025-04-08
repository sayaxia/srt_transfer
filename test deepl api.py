import requests

url = 'https://api-free.deepl.com/v2/translate'
payload = {
    'auth_key': 'c2d88d69-0d33-4469-b336-5a6a7e72f15c:fx',  # 请替换为你的实际 API 密钥
    'text': 'Hello, world!',
    'target_lang': 'ZH'  # 目标语言设为中文
}

response = requests.post(url, data=payload)
try:
    response.raise_for_status()  # 若请求有错误，此行将抛出异常
    print(response.json())
except requests.exceptions.HTTPError as e:
    print("错误详情:", response.text)
    print("HTTP 错误:", e)
