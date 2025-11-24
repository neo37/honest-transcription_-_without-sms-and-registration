from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import uuid
from pathlib import Path
import aiohttp
import asyncio
import json

app = FastAPI(title="Image Generator API")

# Директория для сохранения изображений
IMAGES_DIR = Path("/var/www/generated_images")
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# Используем Hugging Face API для генерации изображений
# Можно использовать бесплатный API или локальную модель
HF_API_URL = "https://api-inference.huggingface.co/models/runwayml/stable-diffusion-v1-5"
HF_API_TOKEN = os.getenv("HF_API_TOKEN", "")  # Опционально, для бесплатного API не нужен

class GenerateRequest(BaseModel):
    prompt: str
    negative_prompt: str = "blurry, low quality, distorted"

@app.get("/")
async def root():
    return {"message": "Image Generator API", "status": "running"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/generate")
async def generate_image(request: GenerateRequest):
    """
    Генерирует изображение по промпту и возвращает ссылку на него
    """
    try:
        # Генерируем уникальное имя файла
        image_id = str(uuid.uuid4())
        image_path = IMAGES_DIR / f"{image_id}.jpg"
        
        # Подготовка запроса к Hugging Face API
        headers = {}
        if HF_API_TOKEN:
            headers["Authorization"] = f"Bearer {HF_API_TOKEN}"
        
        payload = {
            "inputs": request.prompt,
            "parameters": {
                "negative_prompt": request.negative_prompt,
                "num_inference_steps": 20,  # Меньше шагов для быстрой генерации
                "guidance_scale": 7.5
            }
        }
        
        # Отправляем запрос к API
        max_retries = 3
        retry_delay = 10
        
        async with aiohttp.ClientSession() as session:
            for attempt in range(max_retries):
                async with session.post(
                    HF_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as response:
                    if response.status == 503:
                        # Модель загружается, ждем и повторяем
                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_delay)
                            continue
                        else:
                            raise HTTPException(
                                status_code=503,
                                detail="Model is loading, please try again in a moment"
                            )
                    elif response.status != 200:
                        error_text = await response.text()
                        try:
                            error_json = await response.json()
                            error_detail = error_json.get("error", error_text)
                        except:
                            error_detail = error_text
                        raise HTTPException(
                            status_code=response.status,
                            detail=f"API error: {error_detail}"
                        )
                    else:
                        # Проверяем, что это изображение
                        content_type = response.headers.get("Content-Type", "")
                        if "image" in content_type:
                            image_data = await response.read()
                        else:
                            # Может быть JSON с ошибкой
                            try:
                                error_json = await response.json()
                                raise HTTPException(
                                    status_code=500,
                                    detail=f"API returned error: {error_json}"
                                )
                            except:
                                image_data = await response.read()
                        break
        
        # Сохраняем изображение
        with open(image_path, "wb") as f:
            f.write(image_data)
        
        # Возвращаем ссылку на изображение
        image_url = f"https://voice.rity.lol/images/{image_id}.jpg"
        
        return {
            "status": "success",
            "image_url": image_url,
            "image_id": image_id
        }
        
    except aiohttp.ClientError as e:
        raise HTTPException(status_code=500, detail=f"Network error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating image: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

