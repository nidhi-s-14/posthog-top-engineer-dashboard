import requests
import os

headers = {"Authorization": os.getenv("GITHUB_TOKEN")}

r = requests.get("https://api.github.com/rate_limit", headers=headers)

print(r.json())