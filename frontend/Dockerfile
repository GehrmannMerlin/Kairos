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
EXPOSE 80
