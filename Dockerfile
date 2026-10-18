
FROM python:3.11-slim

# কার্যকরী ডিরেক্টরি সেট করা
WORKDIR /app

# আগে requirements.txt কপি করে ইনস্টল করা (যাতে ডকার ক্যাশে কাজে লাগে)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# বাকি সব কোড কপি করা
COPY . .

# Hugging Face Spaces ডিফল্টভাবে 7860 পোর্ট ব্যবহার করে
EXPOSE 7860

# সার্ভার চালু করার কমান্ড
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]