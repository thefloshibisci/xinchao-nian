# rebuild 20260813
# Standalone Xinchao image. This intentionally does not bundle Ombre Brain:
# connect an independently updatable OB deployment through OMBRE_MCP_URL.
FROM node:20-alpine

WORKDIR /app
COPY xinchao/package.json ./
COPY xinchao/src ./src
COPY xinchao/configs ./configs
RUN mkdir -p /app/state && chown -R node:node /app

USER node
ENV NODE_ENV=production \
    PORT=18110 \
    STATE_PATH=/app/state/state.json \
    TRANSITION_JOURNAL_PATH=/app/state/transitions.jsonl \
    OAUTH_STATE_PATH=/app/state/oauth.json \
    BRIDGE_STATE_PATH=/app/state/bridge-queue.json \
    CABIN_STATE_PATH=/app/state/cabin.json
EXPOSE 18110
VOLUME ["/app/state"]
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD node -e "fetch('http://127.0.0.1:18110/health').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"
CMD ["node", "src/server.js"]
