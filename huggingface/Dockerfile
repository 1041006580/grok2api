# 直接从 GitHub Container Registry 拉取预构建镜像
FROM ghcr.io/1041006580/grok2api:latest

# Hugging Face Spaces 使用 7860 端口
ENV PORT=7860
EXPOSE 7860

# 覆盖启动命令，使用 HF 的端口
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
