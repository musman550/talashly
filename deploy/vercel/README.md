# Vercel front door (free HTTPS + CDN) for Talashly

Vercel cannot run the AI model or the crawler (serverless: no long-running process, small memory, short time limits).
What it *can* do for free: sit in front of your VM, give you `https://<name>.vercel.app`, and cache static files at the edge.

1. Vercel -> Add New Project -> import `musman550/talashly`.
2. Root Directory: `deploy/vercel`. Framework: Other. Deploy.
3. If your VM IP changes, edit the destination in `vercel.json` and push.

Notes: Hobby plan is for non-commercial use. Untested here: long streaming answers through a rewrite - if answers get cut off, keep using the direct VM address.
