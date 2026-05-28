FROM node:22-alpine

WORKDIR /app

# Install deps first for better layer caching
COPY package*.json ./
RUN npm ci

# Copy the rest of the source
COPY . .

EXPOSE 3000

# Dev mode in Docker — the host workflow is `npm run dev` for hot reload;
# Docker is for validating the full stack runs together.
CMD ["npm", "run", "dev"]
