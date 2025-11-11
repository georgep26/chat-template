# Development Process

Below is an outline of the development process for both humans and AI agents.

## Branch Structure

- **main** - stable code, this should be what is on production
- **development** - development branch, these includes all the latest features in development
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

## Process Steps

0. **Initial setup** - setup environment, use "make install"

1. **Define new features** (ideally in GitHub projects but not required)
    - Optional: If using GitHub projects you should add new feature ideas to the "Backlog" tab. Issues should be categoriezed as "Feature" or "Bug". 

2. **Create new feature branch**, get features to run locally, include new tests, deploy features to dev environment for testing/iteration

3. **Submit PR from feature branch to development**
   - Should trigger Agent code review - this should help the human reviewer by providing a pre review. The Agent can catch things like poor code quality or items that don't adhere to code standards (docstrings, too many try except, etc.). Should check that proper testing was created for new features and provide suggestions when tests are insufficient. It should also check that the documentation in the repo was updated based on the new feature (including any diagrams).
   - Should use PR template

4. **After all features are complete for one iteration, deploy development branch to QA**
   - Perform QA testing based on the initially submitted features in GitHub projects (this could be performed by humans and AI agents)
   - Move issues in GitHub project from "In Review" to "Complete"

5. **Once QA looks good, submit PR to main**
   - Agent PR review should look through all new features
   - Human sign off
   - Update CHANGELOG.md with latest release
   - Deploy new code on main to production environment
   - Optional: Update the "report-out.qmd" presentation with notes on the latest release