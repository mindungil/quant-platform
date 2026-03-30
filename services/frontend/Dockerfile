FROM node:20-alpine
WORKDIR /app
COPY services/frontend/package.json ./package.json
COPY services/frontend/package-lock.json* ./
RUN npm install
COPY services/frontend ./
RUN npm run build
CMD ["npm", "run", "start"]
