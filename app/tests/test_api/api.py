import requests

def check_healty():
    response = requests.get("http://127.0.0.1:8000/api/v1/health/healthy")
    if response.status_code == 200:
        print("API is healthy")
    else:
        print("API is not healthy")
        
check_healty()