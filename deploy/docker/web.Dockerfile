# The frontend: built once with Node, then served by nginx, which also forwards /api to
# the API. Build from the repository root: docker build -f deploy/docker/web.Dockerfile .

FROM node:22-alpine AS build
WORKDIR /frontend
# Dependencies first, so this layer is rebuilt only when the lock file changes.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# The unprivileged variant runs as a regular user and listens on 8080.
FROM nginxinc/nginx-unprivileged:1.28-alpine
COPY deploy/docker/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /frontend/dist /usr/share/nginx/html
# Address and port of the API, and the network of the proxy in front of nginx, whose
# X-Forwarded-For is trusted. 0.0.0.0/32 is an address no connection comes from: by default
# nobody is trusted, and the client's address is that of the connection.
ENV API_UPSTREAM=api:8000 TRUSTED_PROXY_CIDR=0.0.0.0/32
# Makes the image's entrypoint export the container's DNS servers for the template.
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1
EXPOSE 8080
