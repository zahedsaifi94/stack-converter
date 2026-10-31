FROM python:3.12-slim
WORKDIR /app
COPY server.py full_articles_cache.json ./
COPY public ./public
RUN mkdir -p media_vault
ENV PORT=8080
EXPOSE 8080
CMD ["python3", "server.py"]
