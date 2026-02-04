# Development Process

Below is an outline of the development process for both humans and AI agents.

## Branch Structure

- **main** - stable code, this should match what is in production. Do not push directly to main, always submit PRs (this should be included in the branch protection rules). Standard process is PRs from development branch, but PRs from hotfix branches are allowed for quick bug fixes.
- **development** - development branch, these includes all the latest features in development. This branch is intended to consolidate all changes for a particular release.
- **feature_branch** - new features
- **hotfix_branch** - quick bug fixes that need to be deployed to production immediately (see Hotfix Process below)

## Pairing with GitHub Project
Ideally we pair our repos with GitHub Projects to enhance task tracking. Below are some features we want in our Github project.

For more information on how to setup the GitHub project, see the gh_project_setup.md file.

Tabs:
- Backlog: All incoming issues go through the backlog. Issues should be tagged as:
    - "Feature": new feature that doesn't currently exist in the application 
    - "Bug": an error or bug identified that needs to be fixed 
    - "Task": general task not related to writing new code
    - "Epic": a large feature that is broken down into smaller issues
    - "Initiative": a majore milestone, typically accomplished on the scale of a month or more
- Triage/Sprint Planning: Table view of issues with issues grouped by Sprint. Allows you to plan what gets done in each sprint. You can also assign tasks to specific individuals (or AI agents).
- Current status: Kanban board grouped by each person. This can be used in standup meetings. Main idea is to provide the current status of development activities and report any blockers. All issues should have a "blocked" tag that is visible in this view and can be used to highlight issues.
- QA Review: Tab with all issues that are in QA review and marked as "In Review"
- My Tasks: Tab with all issues that are assigned to the current user. This tab should include all issues assigned to the user, sorted by priority.

Issue Fields:
Type: Feature, Bug, or Task
Status: 
- Backlog: Idea in the backlog, not processed yet.
- Ready: Issue is ready to be worked on.
- In Progress: Actively being worked 
- In Review: Development is complete, waiting for QA review
- Complete: QA testing is complete, the feature is deployed in production 
Assignee: Person or AI agent assigned to the task.
Sprint: The sprint the issue is assigned to. Should be blank for incomming backlog.
Tags: Includes things like "blocked", "good first issue" (for new developers), "question" (raise a question with the team)

Issue Hierarchy:
The overall hierarchy for issues is: Initiative -> Epic -> Feature/Bug/Task -> Sub-task.
Initiative: A major milestone, typically accomplished on the scale of a month or more.
Epic: A large feature that is broken down into smaller issues.
Feature/Bug/Task: A smaller feature or bug that is part of the epic.
Sub-task: A smaller feature or bug that is part of the feature/bug/task.
For a detailed description of each issue type, see (this article)[https://www.launchnotes.com/blog/initiative-vs-epic-vs-feature-understanding-the-key-differences].

## Deployment Process

0. **Initial setup** - setup environment, use "make dev-env"

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
   - Tests should run automatically with the PR to development. If tests fail, please correct the issue before merging to development.

4. **Once all changes are included on the development branch, submit PR from development to main**
   - Should include at least one human reviewer and one AI agent review (two human reviewers recommended for standard releases)
   - Development branch should be deployed to staging AWS environment and QA testing should be performed
   - Evals and tests will automatically run with the PR to main. If tests fail, the PR cannot be merged to main.
   - Perform QA testing based on the initially submitted features in GitHub projects (this could be performed by humans and AI agents)
   - Move issues in GitHub project from "In Review" to "Complete"

5. **Once all QA testing is complete and evals and tests pass, merge PR from development to main**
   - Update CHANGELOG.md with latest release
   - After merge, main branch should be automatically deployed to production AWS environment
   - Optional: Update the "report-out.qmd" presentation with notes on the latest release

## Hotfix Process

Hotfixes are for critical bugs or issues that need to be fixed and deployed to production immediately, bypassing the standard development → main flow.

### Hotfix Workflow

1. **Create a hotfix branch from main**
   ```bash
   git checkout main
   git pull origin main
   git checkout -b hotfix/description-of-fix
   ```

2. **Develop and test the hotfix**
   - Fix the issue
   - Add tests for the fix
   - Test locally or in dev AWS environment
   - Run tests and evals locally

3. **Submit PR from hotfix branch to main**
   - Should include at least one human reviewer and one AI agent review (two human reviewers recommended when time permits)
   - Hotfix should be deployed to staging AWS environment for quick validation
   - Evals and tests will automatically run with the PR to main
   - Perform focused QA testing on the specific fix

4. **Merge hotfix PR to main**
   - Update CHANGELOG.md with the hotfix details
   - After merge, main branch should be automatically deployed to production AWS environment

5. **Merge main back to development** (CRITICAL STEP)
   - After the hotfix is merged to main, you MUST merge main back to development
   - This ensures development includes the hotfix changes
   - Create a PR from main to development
   - This PR typically requires minimal review since it's just syncing the hotfix
   - Merge the PR to keep development in sync with main

**Important Notes:**
- Hotfixes should be minimal and focused on fixing the specific issue
- Always merge main back to development after a hotfix to prevent the hotfix from being lost in future development work
- Hotfixes should be rare - use the standard development → main flow for most changes