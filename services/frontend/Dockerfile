FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV HOSTNAME=0.0.0.0
ENV PORT=3000
ENV NODE_ENV=production

# Copy standalone server
COPY --from=builder /app/.next/standalone ./

# Copy static assets (CSS, JS chunks) — required for standalone mode
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000
CMD ["node", "server.js"]
