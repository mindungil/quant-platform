FROM node:20-alpine
WORKDIR /app
ENV HOSTNAME=0.0.0.0
ENV PORT=3000
COPY package.json ./package.json
COPY package-lock.json* ./
RUN npm install
COPY . ./
RUN npm run build
CMD ["node", ".next/standalone/server.js"]
