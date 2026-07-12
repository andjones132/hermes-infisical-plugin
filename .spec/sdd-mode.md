Here is how Spec-Driven Development mode works for this project.

First, all specification files are in the ".spec" folder starting from the root of the repository.

The process works by first reading a constitution, and then for each feature, reading a spec, generating a plan based on it, then tasks based on the plan, and then starting implementation in test-driven fashion once the tasks are approved.

Here are more details about where the files for each stage are and how to use them:

- 00-constitution.md
    - read this file before working on any feature for principles that apply to the entire project
    - never change after initial creation unless explicitly told

- [feature-name]/ subfolder:
    - About
        - This is where you will find the feature description, spec, plan, tasks.
        - Perform feature work in a repository branch by the name of "feat/[feature-name]". "[feature-name]" should ideally contain a sequence number, e.g "0001-base" for easier visibility of chronological order.
        - Do not create the plan, or tasks, or write any code, before the user explicitly asks you to go to that stage (e.g. user first approves the plan, then tasks, and then coding can begin).
    - Files
        - 00-idea.md: (not always proviuded) use cases and/or acceptance criteria
        - 01-spec.md: this is the specification (usually written by the user, but not necessarily)
            - what problem is being solved and how is it to be solved
            - user stories
            - Gherkin acceptance criteria
            - technical requirements
            - etc
        - 02-plan.md
            - architecture
            - component design
            - data model
            - dependencies
            - file structure
            - trade-offs
            - success metrics
            - risks and mitigations
        - 03-tasks.md
            - atomic (one file or one behavior)
            - dependency-aware
            - testable (include success criteria)
            - numbered with explicit dependencies
            - format
                [ ] #7 → Create User model (depends: #3)
                [P] #12 → Add drag-and-drop (can run in parallel)
                [✓] #30 → Make window movable (completed)
            - during code implementation
                - Write the test first (if applicable)
                - Write the code
                - Run the tests locally and fix until green
                - Mark the task [✓] with a short result
