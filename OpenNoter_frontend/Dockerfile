# === 前端构建阶段 ===
# Vite 6 + @react-spectrum/s2（style 宏在构建期展开）需要 Node 20+。
# 用 npm + package-lock.json 保证可复现构建。
# BASE_REGISTRY 默认 docker.io，国内拉不到可换 daocloud / 阿里云镜像：
#   docker-compose build --build-arg BASE_REGISTRY=docker.m.daocloud.io
ARG BASE_REGISTRY=docker.io
FROM ${BASE_REGISTRY}/library/node:20-alpine AS builder

# 可由发布 workflow 从 git tag 注入，用于前端 About 页展示版本；未传时由 Vite 回退读取 tauri.conf.json。
ARG VITE_APP_VERSION=
ENV VITE_APP_VERSION=${VITE_APP_VERSION}

WORKDIR /app

# 先复制 manifest 利用依赖层缓存
COPY ./OpenNoter_frontend/package.json ./OpenNoter_frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

# 再复制源代码并构建（tsc -b && vite build）
COPY ./OpenNoter_frontend/ ./
RUN npm run build

# --- 阶段2：使用 nginx 作为静态服务器 ---
# 重新声明 ARG —— buildkit 跨阶段不自动继承
ARG BASE_REGISTRY=docker.io
FROM ${BASE_REGISTRY}/library/nginx:1.25-alpine

RUN rm -rf /etc/nginx/conf.d/default.conf
COPY ./OpenNoter_frontend/deploy/default.conf /etc/nginx/conf.d/default.conf

# 拷贝构建产物
COPY --from=builder /app/dist /usr/share/nginx/html
