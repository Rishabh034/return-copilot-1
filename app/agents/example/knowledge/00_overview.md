# Example domain

Every `.md` file in this directory is concatenated, in filename order, into the
cached half of the system prompt. Number them so the order is explicit.

This is the agent's reference material: how its domain works, what its data
means, and which tool answers which question. It is the largest part of the
cached prefix, so it is read once per process — editing a file here needs a
restart to take effect.
