"""Project domain model.

Responsibility: represent a user project, including its source document, selected
page range, preferences, and lifecycle state.
Workflow: shared domain object used from upload through final video delivery.
Inputs: project creation and update data (future).
Outputs: a project aggregate consumed by services and API schemas (future).
Interacts with: user preferences, lesson plan, generation job, and storage.
No model class is defined yet.
"""
