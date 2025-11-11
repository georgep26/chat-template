Review the git history on the current branch and update the CHANGELOG.md file with a new version entry based on the commit messages.

## Steps:

1. **Get the git history:**
   - First, determine the last version tag or the date of the last entry in CHANGELOG.md
   - Get all commit messages from the current branch since the last release (or since the last CHANGELOG entry)
   - Use `git log` to retrieve commit messages, excluding merge commits

2. **Analyze commit messages:**
   - The project uses conventional commit format (feature, fix, docs, refactor, test, chore, perf)
   - Map commit types to Keep a Changelog categories:
     - `feature:` → **Added** (new features)
     - `fix:` → **Fixed** (bug fixes)
     - `refactor:` → **Changed** (code restructuring)
     - `perf:` → **Changed** (performance improvements)
     - `docs:` → **Added** or **Changed** (documentation updates)
     - `test:` → Usually omit unless significant test infrastructure added
     - `chore:` → Usually omit unless significant build/maintenance changes
   - Look for `BREAKING CHANGE:` in commit bodies and add a **Breaking Changes** section if present
   - Group similar changes together

3. **Determine the new version:**
   - Check the current version in CHANGELOG.md
   - If there are breaking changes, increment the major version
   - If there are new features, increment the minor version
   - If there are only fixes or minor changes, increment the patch version
   - Use today's date for the version entry

4. **Update CHANGELOG.md:**
   - Add a new version entry at the top (after [Unreleased] if it exists, or create it)
   - Format: `## [X.Y.Z] - YYYY-MM-DD`
   - **Add a Summary section first** (### Summary) with a brief 1-2 sentence overview of the changes in this release
   - Add categorized sections (### Added, ### Changed, ### Fixed, etc.) only if there are items in that category
   - Use bullet points with concise descriptions based on commit messages
   - Keep descriptions clear and user-focused (not too technical)
   - Move any items from [Unreleased] to the new version entry if applicable
   - Maintain the Keep a Changelog format

5. **Example format:**
   ```
   ## [Unreleased]
   
   ## [1.1.0] - 2025-01-15
   ### Summary
   This release introduces new features for enhanced functionality and includes several performance improvements and bug fixes.
   
   ### Added
   - New feature description based on commit message
   - Another new feature
   
   ### Changed
   - Performance improvement description
   - Refactoring description
   
   ### Fixed
   - Bug fix description
   
   ## [1.0.0] - 2025-10-07
   ```

Make sure to:
- Only include meaningful changes (exclude trivial commits like typo fixes in comments unless significant)
- Consolidate similar commits into single bullet points
- Use present tense and active voice
- Keep descriptions concise but informative

## Documented Process Example

Here's the actual process used in a recent update:

### 1. Read Current CHANGELOG
First, read the existing CHANGELOG.md to determine the last version and date:
```bash
cat CHANGELOG.md
```

### 2. Get Git History
Get a quick overview of recent commits (excluding merges):
```bash
git log --oneline --no-merges --decorate | head -50
```

### 3. Get Detailed Commit Information
Get detailed commit messages with dates since the last release. First, identify the last release date from CHANGELOG.md (e.g., 2025-10-07), then:
```bash
git log --format="%H|%ai|%s|%b" --no-merges --since="2025-10-07" | head -100
```
This provides:
- `%H`: Full commit hash
- `%ai`: Author date in ISO format
- `%s`: Subject (commit message)
- `%b`: Body (for BREAKING CHANGE detection)

### 4. Get Current Date
Get today's date for the new version entry:
```bash
date +%Y-%m-%d
```

### 5. Analyze and Categorize Commits
Review the commit messages and categorize them:
- Commits starting with `feature:` → **Added**
- Commits starting with `fix:` → **Fixed**
- Commits with `Refactor` or `refactor:` → **Changed**
- Commits with `Update` or `docs:` → **Changed** or **Added** (depending on context)
- Commits with `Add` → **Added**
- Check commit bodies for `BREAKING CHANGE:` → **Breaking Changes**

### 6. Determine Version Number
- Last version: 1.0.0
- New features present → Increment minor version → 1.1.0
- No breaking changes → Keep major version at 1

### 7. Update CHANGELOG.md
Add the new version entry after `[Unreleased]` with:
- Version number and date
- Summary section (1-2 sentences)
- Categorized sections (Added, Changed, Fixed) with bullet points
- User-focused, concise descriptions

### Example Commits Processed
From the example run:
- `feature: Provide detailed instructions...` → **Added**: CHANGELOG update instructions
- `fix: update commit message generation rules...` → **Fixed**: Commit message generation rules
- `Add AGENT.md: Introduce instructions...` → **Added**: AGENT.md with instructions
- `Refactor application configuration...` → **Changed**: Configuration refactor
- `Update Python version...` → **Changed**: Python version update
- `feature: add cursor rules...` → **Added**: Cursor rules
- `Update README.md...` → **Changed**: README updates
- Various cleanup commits → Grouped or omitted if trivial