FROM python:3.11

# unbuffered stdout so startup prints ([CREDS] etc.) appear in docker logs immediately
ENV PYTHONUNBUFFERED=1

WORKDIR /home/ubuntu/clickpost-ai-projects

COPY ./requirements.txt .
RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]