# Frontend image: build the React app with Node, serve it with nginx.
# Build context = the repository root ("workshop hackthon/"):
#   docker build -f other/docker/Dockerfile -t sales-dw-frontend .
# nginx also proxies /api/* to the backend service, so the browser uses one origin.

FROM node:22-alpine AS build
WORKDIR /app
# Optional extra root CAs (antivirus HTTPS scanning / corporate proxy); see certs/README.md.
COPY other/docker/certs/ /tmp/extra-certs/
RUN cat /tmp/extra-certs/*.crt > /tmp/extra-ca.pem 2>/dev/null || true
COPY frontend/package.json frontend/package-lock.json ./
RUN if [ -s /tmp/extra-ca.pem ]; then export NODE_EXTRA_CA_CERTS=/tmp/extra-ca.pem; fi \
    && npm ci --no-audit --no-fund
COPY frontend/ ./
# Empty base URL = same origin; nginx forwards /api to the backend container.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN rm -f .env.local && npm run build

FROM nginx:1.27-alpine
COPY other/docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
