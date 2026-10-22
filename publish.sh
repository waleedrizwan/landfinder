#!/bin/zsh
# Deploy site/ to Cloudflare Pages (project "cheapest-land"). Needs: npx wrangler login (done once).
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
npx -y wrangler@4 pages deploy site --project-name cheapest-land --branch main --commit-dirty=true
