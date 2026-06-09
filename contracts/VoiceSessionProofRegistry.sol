// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

/// @title VoiceSessionProofRegistry
/// @notice Minimal registry for Pharos Voice Action Gateway proof hashes.
/// @dev This contract intentionally stores hashes and decisions only. Raw audio,
/// transcripts, private keys, and wallet credentials stay off-chain.
contract VoiceSessionProofRegistry {
    struct VoiceMandateProof {
        string actionId;
        string action;
        bytes32 voiceHash;
        bytes32 intentHash;
        bytes32 mandateHash;
        bytes32 policyHash;
        bytes32 challengeHash;
        string subject;
        address submitter;
        uint256 recordedAt;
        bytes signature;
    }

    mapping(bytes32 => VoiceMandateProof) private proofs;
    mapping(bytes32 => bool) public recorded;

    event VoiceMandateRecorded(
        bytes32 indexed mandateHash,
        bytes32 indexed voiceHash,
        bytes32 indexed intentHash,
        string actionId,
        string action,
        string subject,
        address submitter,
        uint256 recordedAt
    );

    error ProofAlreadyRecorded(bytes32 mandateHash);
    error EmptyMandateHash();

    function recordVoiceMandate(
        string calldata actionId,
        string calldata action,
        bytes32 voiceHash,
        bytes32 intentHash,
        bytes32 mandateHash,
        bytes32 policyHash,
        bytes32 challengeHash,
        string calldata subject,
        bytes calldata signature
    ) external returns (bytes32) {
        if (mandateHash == bytes32(0)) {
            revert EmptyMandateHash();
        }
        if (recorded[mandateHash]) {
            revert ProofAlreadyRecorded(mandateHash);
        }

        proofs[mandateHash] = VoiceMandateProof({
            actionId: actionId,
            action: action,
            voiceHash: voiceHash,
            intentHash: intentHash,
            mandateHash: mandateHash,
            policyHash: policyHash,
            challengeHash: challengeHash,
            subject: subject,
            submitter: msg.sender,
            recordedAt: block.timestamp,
            signature: signature
        });
        recorded[mandateHash] = true;

        emit VoiceMandateRecorded(
            mandateHash,
            voiceHash,
            intentHash,
            actionId,
            action,
            subject,
            msg.sender,
            block.timestamp
        );
        return mandateHash;
    }

    function getVoiceMandate(bytes32 mandateHash) external view returns (VoiceMandateProof memory) {
        return proofs[mandateHash];
    }
}
