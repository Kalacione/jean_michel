# Frontend container (S8) — build the Vue/Vuetify SPA, serve it via nginx and
# reverse-proxy /api + /ws to the hand-launched daemon on the host.
# Single origin (nginx) → no CORS.

# ---- build stage ----------------------------------------------------------
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# ---- serve stage ----------------------------------------------------------
FROM nginx:alpine AS serve
# Templated server config : ${DAEMON_ADDR} is substituted at container start by
# the nginx image's envsubst entrypoint (nginx's own $vars are left untouched).
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
# Where the host daemon lives, as seen from inside the container. On Linux run
# with `--add-host=host.docker.internal:host-gateway` (compose: extra_hosts).
ENV DAEMON_ADDR=host.docker.internal:8000
EXPOSE 80
