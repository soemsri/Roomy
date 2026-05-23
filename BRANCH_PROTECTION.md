# Roomy - GitHub Branch Protection Guide

To guarantee code quality and enforce peer code reviews before merging, we recommend setting up **GitHub Branch Protection Rules** on your repository. 

Follow this step-by-step guide to secure your main branches (e.g., `main`, `master`, or `develop`).

---

## 🛠️ Step-by-Step Setup Instructions

1. **Navigate to Settings**:
   - Go to your repository on [GitHub](https://github.com).
   - Click on the **Settings** tab (represented by a gear icon) at the top of the repository page.

2. **Access Branch Protection Rules**:
   - In the left sidebar, locate the **Code and automation** section.
   - Click on **Branches**.

3. **Add a Protection Rule**:
   - Click on the **Add branch protection rule** button (or click **Edit** if a rule already exists for your main branch).

4. **Define Branch Pattern**:
   - Under **Branch name pattern**, enter the name of the branch you want to protect (e.g., `main`, `master`, or `develop`).

5. **Configure Protection Rules (Recommended Settings)**:
   
   ✅ **Require a pull request before merging**:
   - Tick this box. This blocks any developer (including you) from pushing code directly to the branch.
   - Tick **Require approvals**.
   - Under **Required number of approvals before merging**, select **1** (or more depending on team size).
   
   ✅ **Require status checks to pass before merging**:
   - Tick this box. This ensures that your CI/CD test suite passes before a merge is allowed.
   - Tick **Require branches to be up to date before merging** (forces developers to pull latest changes from main first).
   - In the search bar under *Status checks that must pass*, search for:
     ```text
     Lint & Test Suite
     ```
     *(Note: This check name is defined in your `ci.yml` file and will appear here after the GitHub Action has run successfully at least once).*
     Select it once it appears.

   ✅ **Do not allow bypassing the above settings**:
   - Tick this box if you want to enforce these rules on repository administrators (owners) as well.

6. **Save Changes**:
   - Click the green **Create** or **Save changes** button at the bottom of the page.
   - *(GitHub may ask for your password to confirm).*

---

## 🔄 How the Workflow Operates Now

Once configured:
1. **Branch Block**: Nobody can push code directly to `main` or `master`. All work must be done on feature branches.
2. **Open Pull Request**: When opening a Pull Request (PR), the **PR Template** checklist automatically prompts the author.
3. **CI/CD Triggers**: GitHub Actions immediately runs the linter and test suite.
4. **Merge Blocked**: The "Merge Pull Request" button is **disabled** and grayed out until:
   - The `Lint & Test Suite` status check completes with a **green tick** ✅.
   - Another team member reviews and **approves** the PR 💬.
