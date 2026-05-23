# HELM — High-conviction Entry & Lifecycle Manager

A Claude-native options trading system. Built in parallel to COTS v1.0.

## Vision
HELM is a conversation-first trading platform where Claude is the co-pilot.
The UI is the conversation. The CLI is the engine.

## Firewall Rules
- This repo never touches ~/Projects/cots (COTS v1.0)
- COTS v1.0 server: http://cots.local:8765
- HELM server: http://helm.local:8766
- Separate Claude project: HELM
- Never mixed in the same trading session

## Strategies
- [ ] Cash-Secured Puts (CSP)
- [ ] Long Calls
- [ ] Covered Calls
- [ ] PERM (Pre-Earnings Run-up)
- [ ] Bull Put Spread
- [ ] Iron Condor
- [ ] Diagonal Spread

## Status
- [x] Repo initialized
- [x] Server running (helm.local:8766)
- [ ] Data model design
- [ ] Core infrastructure
- [ ] Strategy implementations
- [ ] Claude integration layer
- [ ] Setup/onboarding flow
