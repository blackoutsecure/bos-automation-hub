# Marketplace Repository Ruleset Template

`main-protection-ruleset.json` is a declarative GitHub organization ruleset template for Marketplace Action repositories. It protects the default branch from deletion, force pushes, and workflow-file changes while allowing an explicitly configured release integration to bypass the path restriction.

Before applying the template, replace the bypass actor placeholder and repository list with approved values. Validate the resulting ruleset in a non-production repository first. This directory intentionally contains no script that applies rulesets.

This template is defense in depth for Marketplace artifact path restrictions. It does not replace a repository branch-protection policy and does not satisfy `PS020` on its own. Repositories that use the code-scanning policy must separately configure branch protection with the required reviews, status checks, force-push restriction, and any configured conversation-resolution requirement.
