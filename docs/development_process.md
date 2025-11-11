# Development Process 

Below is an outline of the development process for both humans and AI agents.

main - stable code, this should be what is on production 
development - development branch, these includes all the latest features in development 
feature_branch - new features



0. Intial setup - setup environment 
1. Define new features (ideally in GitHub projects)
2. Create new feature branch, get features to run locally, include new tests, deploy features to dev environment for testing/iteration 
3. Submit PR from feature branch to development. 
    - Should trigger Agent code review - this should help the human reviewer by providing a pre review. The Agent can catch things like poor code quality or items that don't adhere to code standards (docstrings, too many try except, etc.). Should check that proper testing was created for new features and provide suggestions when tests are insufficient. It should also check that the documentation in the repo was updated based on the new feature (including any diagrams).
    - Should use PR template 
4. After all features are complete for one iteration, deploy development branch to QA
    - Perform QA testing based on the initially submitted features in GitHub projects (this could be performed by humans and AI agents)
    - Move issues in GitHub project from "In Review" to "Complete" 
4. Once QA looks good, submit PR to main 
    - Agent PR review should look through all new features
    - Human sign off 
    - Update CHANGELOG.md with latest release 
    - Deploy new code on main to production environment
    - Optional: Update the "report-out.qmd" presentation with notes on the latest release 