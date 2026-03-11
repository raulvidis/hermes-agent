// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {AgentScore} from "../src/AgentScore.sol";

contract DeployAgentScore is Script {
    function run() external {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);

        console.log("Deployer:", deployer);
        console.log("Chain ID:", block.chainid);

        vm.startBroadcast(deployerKey);
        AgentScore agentScore = new AgentScore();
        vm.stopBroadcast();

        console.log("AgentScore deployed at:", address(agentScore));
    }
}
