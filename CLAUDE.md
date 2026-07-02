@AGENTS.md

## Claude Code

- Use the imported `AGENTS.md` instructions as the shared project guidance.
- Project skills live under `.claude/skills/` and should delegate to shared prompts under `ai_agents/prompts/`.

## Frontend deploy

The frontend lives in the sibling repo `../chat-template-frontend`. Deploying to CloudFront is a **two-step** process split across the repos:

1. **Infrastructure** (CloudFormation S3 + CloudFront): from this repo, run `./scripts/deploy/resources/frontend.sh <env> -y` (alias for `./scripts/deploy/deploy_frontend.sh <env>`). This only updates the stack.
2. **Build + upload + invalidation**: from `../chat-template-frontend`, run `./infrastructure/scripts/deploy.sh <env> --aws-profile <profile> -y`. This is the step that ships actual frontend code changes (`nuxt generate` → `aws s3 sync` → CloudFront invalidation).

Step 1 alone won't push code changes. See `../chat-template-frontend/CLAUDE.md` for full details.
