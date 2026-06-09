from typing import Any
from collections import defaultdict, deque


CONSTRAINT_AGENT_MAP = {
    # "budget": ["accommodation_agent"],
    # "preference": ["accommodation_agent"],
    # #"area": ["accommodation_agent"],
    # #"hotel_style": ["accommodation_agent"],

    # "direct_flight": ["transport_agent"],
    # "airline_preference": ["transport_agent"],

    # "pace": ["attraction_agent"],
    "transport": "transport_agent",
    "accommodation": "accommodation_agent",
    "attraction": "attraction_agent"
}

# From Cursor
AGENT_DEPENDENCY_MAP = {
    # If transport changes, downstream accommodation_agent must rerun
    "transport_agent": ["accommodation_agent"],  #attraction_agent
    # If accommodation changes, downstream attractions must rerun
    "accommodation_agent": ["attraction_agent"],
    # If you later add more, e.g. restaurant agent that depends on accommodation:
    # "accommodation_agent": ["attraction_agent", "restaurant_agent"],
}

# From ChatGPT and myself
AGENT_DEPENDENCIES = {
    "transport_agent": [],
    "accommodation_agent": ["transport_agent"],
    "attraction_agent": ["accommodation_agent"],
}


# Obsolete
def propagate_agent_dependencies(initial_dirty: set[str]) -> list[str]:
    """
    Given initial dirty agents,
    propagate downstream dependencies.
    Return ordered list (topological).
    """

    visited = set[Any]()
    result = []

    def dfs(agent):
        if agent in visited:
            return
        visited.add(agent)

        # Append itself first
        result.append(agent)

        # Then propagate to its dependents
        for dependent in AGENT_DEPENDENCY_MAP.get(agent, []):
            dfs(dependent)

    for agent in initial_dirty:
        dfs(agent)

    return result


def propagate_dirty_agents(dirty_agents: set[str]) -> set[str]:
    """
    Given a set of dirty agents,
    propagate downstream dependencies.
    Return a set of all dirty agents.
    """
    expanded = set(dirty_agents)

    changed = True

    while changed:
        changed = False

        for agent, deps in AGENT_DEPENDENCIES.items():

            if any(dep in expanded for dep in deps):
                if agent not in expanded:
                    expanded.add(agent)
                    changed = True

    return expanded


def topo_sort_agents(agents: set[str]) -> list[str]:
    """
    Given a set of agents,
    return a topological order of the agents.
    """

    indegree = defaultdict(int)
    graph = defaultdict(list)

    for agent in agents:
        for dep in AGENT_DEPENDENCIES.get(agent, []):
            if dep in agents:
                graph[dep].append(agent)
                indegree[agent] += 1

    queue = deque([a for a in agents if indegree[a] == 0])

    order = []

    while queue:

        node = queue.popleft()
        order.append(node)

        for neigh in graph[node]:
            indegree[neigh] -= 1

            if indegree[neigh] == 0:
                queue.append(neigh)

    return order