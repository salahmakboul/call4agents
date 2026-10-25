# Roadmap

What we plan, in the order we plan it. Nothing here has a promised date. Each item says what would prove it
useful; tell us in [discussions](https://github.com/salahmakboul/call4agents/discussions) if your agent needs
something else more.

## Now

- **Approval tickets used by real people.** Tickets work with plain Python, Pydantic AI, the OpenAI Agents SDK
  and LangGraph (0.5, experimental, tested with fake models). We need reports from real approval UIs.
- **Making the first five minutes work.** Copy-and-run examples that print what they do, and clear messages
  when something goes wrong.

## Next

- **Tickets and the shared record out of "experimental" and "beta"**, once real users have run them and told
  us what broke.
- **A "done so far" summary from the record**: what already ran, what is in doubt, what waits for a person,
  so an agent that restarts or loses its context does not ask for the same action again. Only if users ask
  for it.

## Not planned

- A memory store or a hosted service. Other projects do this well; Call4Agents stays a small library for the
  safety of an agent's real-world actions, with no required dependencies.

## Done

See [CHANGELOG.md](CHANGELOG.md).
