# GitHub Project Setup

This guide explains how to set up the **Chat Template Project** board for tracking work (tasks, features, epics, initiatives) with backlogs, sprints, and workflows.

## Recommended: Copy the Existing Project

**The easiest way to get the same setup is to copy the project in GitHub.**

1. Open the **Chat Template Project** in your repository (or the template repo).
2. Click the **⋯** (three dots) or **Settings** for the project.
3. Choose **Copy project** (or **Duplicate**).
4. Name the new project (e.g. "Chat Template Project") and choose the owner/org and repository.
5. After the copy, link the new project to your repo’s issues and adjust any filters or workflows if needed.

Copying preserves:
- All views (Backlog, Sprint Planning, Daily Management Board, Rollup, Roadmap, In review, My items)
- Custom fields (Status, Type, Iteration, Priority, etc.) and their options
- View filters, grouping, and layout (Table/Board/Roadmap)
- Workflows (if supported by the copy)
- Iteration setup (e.g. Sprint 1)

If you cannot copy (e.g. no access to the template project), use the manual steps below.

---

## Manual Setup

Use these steps when you need to build the project from scratch.

### 1. Create the project

1. In your GitHub repo, go to **Projects** and click **New project**.
2. Choose **New project** (not from a template, unless you want a different base).
3. Name it (e.g. **Chat Template Project**).

### 2. Add and configure custom fields

Open **+ New field** (or the field/settings area) and ensure these fields exist and are configured as below.

#### Status (single-select)

Used for workflow stages. Add these options:

| Option       | Description (optional)              |
|-------------|--------------------------------------|
| Backlog     | This item hasn't been started       |
| In progress | This is actively being worked on    |
| In review   | This item is in review              |
| Done        | This has been completed             |

#### Type (single-select)

Used to distinguish work item kinds. Add:

- **Task**
- **Feature**
- **Epic**
- **Initiative**

#### Other fields to have visible or available

- **Assignees** (built-in)
- **Iteration** (for sprints; create iterations in project settings)
- **Priority** (single-select, e.g. P1, P2, P3)
- **Parent issue** (for hierarchy)
- **Sub-issues progress** (rollup)
- **Size** (optional)
- **Estimate** (optional number)
- **Start Date** / **Target Complete Date**
- **Labels**, **Linked pull requests**, **Milestone**, **Repository**, **Reviewers** (show or hide per view)

Use the **Visible fields** / **Hidden fields** list (under **+ New field** or the view’s **Fields** control) to choose which columns appear in each view.

### 3. Create views and set filters/layout

Create each view via **+ New view**, name it, then set the **filter** and **layout** as below.

#### Backlog

- **Filter:** `status:Backlog, -type:Initiative, -type:Epic`
- **View:** Table
- **Visible fields (suggested):** Title, Status, Type, Assignees, Iteration, Parent issue, Priority, Sub-issues progress, Size, Estimate, Start Date, Target Complete Date
- **Group by:** e.g. Priority (or none)
- **Sort by:** Status (or manual)

#### Sprint Planning – Triage

- **Filter:** `-type:Initiative`
- **View:** Table
- **Group by:** Iteration
- **Sort by:** Manual
- **Show hierarchy (Beta):** On
- **Visible fields (suggested):** Title, Iteration, Assignees, Milestone, Status, Target Complete Date, Start Date, Estimate

#### Daily Management Board

- **Filter:** `iteration:@current, -type:Epic, -type:Initiative`
- **View:** Board
- **Column by:** **Status** (Backlog | In progress | In review | Done)
- **Swimlanes:** Assignees (optional)
- **Sort by:** Priority (ascending)
- **Field sum:** Count, Estimate
- **Visible fields on cards (suggested):** Title, Assignees, Linked pull requests, Sub-issues progress, Estimate, Iteration, Labels, Size, Priority

#### Rollup

- **Filter:** `type:Initiative`
- **View:** Table
- **Show hierarchy (Beta):** On
- **Visible fields (suggested):** Title, Sub-issues progress, Type, Status

Use this view to see Initiatives → Epics → Tasks/Features and progress rollups.

#### Roadmap

- **Filter:** `type:Epic` (or include Initiatives if your roadmap shows both)
- **View:** Roadmap
- **Date fields:** Start Date and Target Complete Date (or your chosen date fields)
- Use **Markers**, **Sort**, **Date fields**, **Quarter**, **Today** as needed for timeline and current week.

#### In review

- **Filter:** `status:"In review"`
- **View:** Table
- **Visible fields (suggested):** Title, Assignees, Linked pull requests, Sub-issues progress, Reviewers, Repository
- **Group by:** none
- **Sort by:** manual

#### My items

- **Filter:** `assignee:@me`
- **View:** Table or Board (same as Daily Management if you prefer)
- **Visible fields:** As needed (e.g. Title, Priority, Linked pull requests, Status)

### 4. Iterations (sprints)

1. In project **Settings** (or the Iteration field settings), open **Iterations**.
2. Create iterations (e.g. **Sprint 1**, **Sprint 2**) and set date ranges.
3. Mark the current sprint (e.g. **Current**) so `iteration:@current` works in filters.

### 5. Workflows (optional)

Under **Workflows**, you can add automations, for example:

- Set **Status** to **In progress** when an issue is assigned.
- Set **Status** to **In review** when a linked PR is opened.
- Set **Status** to **Done** when a linked PR is merged.

The reference project uses **6** workflows; add or adjust to match your process.

### 6. Linking issues to the project

- From an issue: use **Projects** in the right sidebar and add the issue to **Chat Template Project**.
- From the project: use **+ Add item** and search for issues, or add **New item** and create a draft issue (then convert to an issue in your repo).

---

## Summary

| Approach   | When to use |
|-----------|-----------------------------|
| **Copy project** | You have access to the existing Chat Template Project; fastest and keeps all views, fields, and workflows. |
| **Manual setup** | You can’t copy; follow the steps above to recreate views, fields, filters, and optional workflows. |

After setup, use **Backlog** for triage, **Sprint Planning – Triage** for assigning work to iterations, **Daily Management Board** for day-to-day status, **Rollup** for initiative/epic progress, and **Roadmap** for timeline planning.
