# Development Process

Below is an outline of the development process for both humans and AI agents.

## Branch Structure

- **main** - stable code, this should match what is on production.
- **development** - development branch, these includes all the latest features in development. This branch is intended to consolidate all changes for a particular release.
- **feature_branch** - new features

## Pairing with GitHub Project
Ideally we pair our repos with GitHub Projects to enhance task tracking. Below are some features we want in our Github project.

Tabs:
- Backlog: All incoming issues go through the backlog. Issues should be tagged as:
    - "Feature": new feature that doesn't currently exist in the application 
    - "Bug": an error or bug identified that needs to be fixed 
    - "Task": general task not related to writing new code
- Sprint Planning: Table view of issues with issues grouped by Sprint. Allows you to plan what gets done in each sprint. You can also assign tasks to specific individuals (or AI agents).
- Current status: Kanban board grouped by each person. This can be used in standup meetings. Main idea is to provide the current status of development activities and report any blockers. All issues should have a "blocked" tag that is visible in this view and can be used to highlight issues.
- QA Review: Tab with all issues that are in QA review and marked as "In Review"

Issue Fields:
Type: Feature, Bug, or Task
Status: 
- Backlog: Idea in the backlog, not processed yet.
- In Progress: Actively being worked 
- In Review: Development is complete, waiting for QA review
- Complete: QA testing is complete, the feature is deployed in production 
Assignee: Person or AI agent assigned to the task.
Sprint: The sprint the issue is assigned to. Should be blank for incomming backlog.
Tags: Includes things like "blocked", "good first issue" (for new developers), "question" (raise a question with the team)

## Deployment Process

0. **Initial setup** - setup environment, use "make install"

1. **Define new features** (ideally in GitHub projects but not required)
    - Optional: If using GitHub projects you should add new feature ideas to the "Backlog" tab. Issues should be categoriezed as "Feature" or "Bug". 

2. **Create a feature branch from main or development**
   - Develop new feature locally
   - Test features locally or in dev AWS environment
   - Run tests and evals locally before submitting PR to development

3. **Submit PR from feature_branch to development**
   - Should include a review from one human and one AI agent
   - AI agent review should help the human reviewer by providing a pre review. The Agent can catch things like poor code quality or items that don't adhere to code standards (docstrings, too many try except, etc.). Should check that proper testing was created for new features and provide suggestions when tests are insufficient. It should also check that the documentation in the repo was updated based on the new feature (including any diagrams).
   - Should use PR template

4. **Once all changes are included on the development branch, submit PR from development to main**
   - Should include two human reviewers and one AI agent review
   - Development branch should be deployed to staging AWS environment and QA testing should be performed
   - Evals and tests will automatically run with the PR to main
   - Perform QA testing based on the initially submitted features in GitHub projects (this could be performed by humans and AI agents)
   - Move issues in GitHub project from "In Review" to "Complete"

5. **Once all QA testing is complete and evals and tests pass, merge PR from development to main**
   - Update CHANGELOG.md with latest release
   - After merge, main branch should be automatically deployed to production AWS environment
   - Optional: Update the "report-out.qmd" presentation with notes on the latest release