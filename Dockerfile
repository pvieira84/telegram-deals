FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py core.py media.py schedule.py resolver.py ./
ENTRYPOINT ["python", "app.py"]
CMD ["run"]
