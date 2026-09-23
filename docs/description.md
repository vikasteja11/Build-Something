# Project Description

## What does this software do?

This application is a **multi-agent code generation and review system**. A user provides a plain-text coding specification (e.g., "write a Python function that reverses a string"), and two AI-powered agents collaborate to produce and quality-check the code:

1. **Coder Agent** receives the specification, generates a code snippet using an LLM via OpenRouter (e.g., `nvidia/nemotron-3-super-120b-a12b:free`), and then automatically forwards the result to the Reviewer Agent for quality assurance.

2. **Reviewer Agent** receives the generated code (along with the original specification for context) and performs an automated code review using a separate LLM call. It evaluates the code for correctness, potential bugs, style issues, and adherence to the specification, then returns a list of review comments and a verdict — either **"pass"** (acceptable quality) or **"needs-work"** (significant issues found).

The full pipeline result — original spec, generated code, review comments, and verdict — is persisted in a shared PostgreSQL database so the user can query the complete history of all spec→code→review runs.

## Why two agents?

The system is deliberately split into two independent microservices rather than a single monolith for several reasons:

### Separation of concerns
Code generation and code review are fundamentally different tasks requiring different LLM prompting strategies, evaluation criteria, and system prompts. Separating them into dedicated services makes each one simpler to understand, test, and maintain.

### Independent scalability
In a real-world scenario, code review may take longer or be more compute-intensive than code generation (or vice versa). With two separate Kubernetes Deployments, each can be scaled independently — you could run 2 coder-agent replicas but 5 reviewer-agent replicas if review is the bottleneck, without wasting resources.

### Reusability
Each agent exposes its own REST API, so they can be used independently:
- The coder-agent can generate code without requesting a review.
- The reviewer-agent can review code that was written by a human, not just by the coder-agent.

### Fault isolation
If the reviewer-agent crashes or becomes temporarily unavailable, the coder-agent still functions and can return generated code to the user — it just won't include a review. The system degrades gracefully rather than failing entirely.

### Demonstration of microservice patterns
This project demonstrates real-world microservice architecture patterns: service-to-service REST communication, shared database access, Kubernetes service discovery, independent deployment, and horizontal scaling — all of which are relevant to the course objectives.
