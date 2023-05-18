import React from 'react';
import { connect } from 'react-redux';
import { makeStyles} from '@material-ui/core/styles';
import AppBar from '@material-ui/core/AppBar';
import Menu from './Menu';

import { toggleMenu } from '../../actions/generalActions';

const useStyles = makeStyles({
  menuButton: {
    marginLeft: -12,
    marginRight: 20,
  },
});

const Header = props => {
  const classes = useStyles();

  const toggleMenu = event => {
    props.toggleMenu(true);
  };

  return (
    <div>
      <AppBar position={'static'} color={'primary'}>
        <Menu />
      </AppBar>
    </div>
  );
};

export default connect(null, { toggleMenu })(Header);
