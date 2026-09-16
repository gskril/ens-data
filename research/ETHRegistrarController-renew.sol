// MIT; ensdomains/ens-contracts 121dc232df06be28e5eabb34ebfc4bc788498c05
// Deployed address: 0x59E16fcCd424Cc24e280Be16E11Bcd56fb0CE547
    function renew(
        string calldata label,
        uint256 duration,
        bytes32 referrer
    ) external payable override {
        bytes32 labelhash = keccak256(bytes(label));

        IPriceOracle.Price memory price = _rentPrice(
            label,
            labelhash,
            duration
        );
        if (msg.value < price.base) revert InsufficientValue();

        uint256 expires = base.renew(uint256(labelhash), duration);

        emit NameRenewed(label, labelhash, price.base, expires, referrer);

        if (msg.value > price.base)
            payable(msg.sender).transfer(msg.value - price.base);
    }

    /// @notice Withdraws the balance of the contract to the owner.
