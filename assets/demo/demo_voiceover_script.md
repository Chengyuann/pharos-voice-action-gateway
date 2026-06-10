# Pharos Voice Action Gateway Demo Voiceover Script

Voice: male, clear product-demo narration.
Duration target: about 70 seconds.

1. Welcome to Pharos Voice Action Gateway, a reusable Skill for safer voice-driven on-chain agents.
2. The problem is simple: voice agents can mishear partial commands, miss interruptions, or execute payments before the user clearly confirms.
3. This Skill first waits for a complete voice turn, then converts that turn into a structured Pharos action.
4. For high-risk actions, it creates a voice mandate, checks policy limits, and asks for explicit confirmation.
5. In the first demo, a user sends zero point zero two PHRS, confirms the action, and the Skill returns a simulated transaction hash.
6. In the second demo, the user never confirms. The action is blocked by the confirmation gate.
7. In the third demo, the user confirms a larger payment, but the amount and recipient violate policy. The transaction is still blocked.
8. The report also exports EIP seven twelve typed data, so a wallet or delegated account can sign the exact VoiceMandate later.
9. Finally, the repository includes a VoiceSessionProofRegistry contract, giving Phase two a clear path to anchor proof hashes on Pharos.
10. The result is a voice-native mandate and policy layer for Pharos agents: complete turns, verifiable intent, safer payments, and auditable proofs.
