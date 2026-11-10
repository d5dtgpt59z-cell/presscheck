FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
ENV BACKEND_URL=http://api:8000
RUN npm run build
EXPOSE 3000
USER node
CMD ["npm","start"]
