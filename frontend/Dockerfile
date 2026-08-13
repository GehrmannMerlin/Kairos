# ---- base: dependencies + source ----
FROM node:24-alpine AS base
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .

# ---- dev: vite dev server (used by the local compose `web` service) ----
FROM base AS dev
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# ---- prod-build: type-check + build static assets ----
FROM base AS prod-build
RUN npm run build

# ---- prod: nginx serving built assets ----
FROM nginx:1.27-alpine AS prod
COPY --from=prod-build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
# OCI annotation: connects the pushed GHCR package to the source repository so
# GITHUB_TOKEN-based pushes can keep writing the same private package (GHCR
# bootstrap pattern). Placed last so it never invalidates the build layers.
# Metadata only — no runtime effect.
LABEL org.opencontainers.image.source=https://github.com/GehrmannMerlin/Kairos
EXPOSE 80
