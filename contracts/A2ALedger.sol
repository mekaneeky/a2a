// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract A2ALedger {
    address public immutable operator;

    mapping(bytes32 => uint256) private balances;
    mapping(bytes32 => uint256) private escrows;

    event Faucet(bytes32 indexed agent, uint256 amount, bytes32 indexed txId);
    event Reserve(bytes32 indexed contractId, bytes32 indexed buyer, uint256 amount, bytes32 txId);
    event Payout(bytes32 indexed contractId, bytes32 indexed seller, uint256 amount, bytes32 txId);
    event Refund(bytes32 indexed contractId, bytes32 indexed buyer, uint256 amount, bytes32 txId);
    event Transfer(
        bytes32 indexed fromAccount,
        bytes32 indexed toAccount,
        uint256 amount,
        bytes32 indexed txId,
        bool allowOverdraft
    );

    modifier onlyOperator() {
        require(msg.sender == operator, "only operator");
        _;
    }

    constructor(address operator_) {
        operator = operator_;
    }

    function faucet(bytes32 agent, uint256 amount, bytes32 txId) external onlyOperator {
        balances[agent] += amount;
        emit Faucet(agent, amount, txId);
    }

    function reserve(bytes32 contractId, bytes32 buyer, uint256 amount, bytes32 txId) external onlyOperator {
        uint256 balance = balances[buyer];
        require(balance >= amount, "insufficient funds");
        unchecked {
            balances[buyer] = balance - amount;
        }
        escrows[contractId] += amount;
        emit Reserve(contractId, buyer, amount, txId);
    }

    function payout(bytes32 contractId, bytes32 seller, bytes32 txId) external onlyOperator {
        uint256 amount = escrows[contractId];
        require(amount > 0, "empty escrow");
        escrows[contractId] = 0;
        balances[seller] += amount;
        emit Payout(contractId, seller, amount, txId);
    }

    function refund(bytes32 contractId, bytes32 buyer, bytes32 txId) external onlyOperator {
        uint256 amount = escrows[contractId];
        require(amount > 0, "empty escrow");
        escrows[contractId] = 0;
        balances[buyer] += amount;
        emit Refund(contractId, buyer, amount, txId);
    }

    function transfer(
        bytes32 fromAccount,
        bytes32 toAccount,
        uint256 amount,
        bytes32 txId,
        bool allowOverdraft
    ) external onlyOperator {
        if (!allowOverdraft) {
            uint256 balance = balances[fromAccount];
            require(balance >= amount, "insufficient funds");
            unchecked {
                balances[fromAccount] = balance - amount;
            }
        }
        balances[toAccount] += amount;
        emit Transfer(fromAccount, toAccount, amount, txId, allowOverdraft);
    }

    function balanceOf(bytes32 account) external view returns (uint256) {
        return balances[account];
    }

    function escrowOf(bytes32 contractId) external view returns (uint256) {
        return escrows[contractId];
    }
}
